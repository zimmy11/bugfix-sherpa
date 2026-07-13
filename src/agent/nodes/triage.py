import json

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from src.agent.state import BugFixingState
from src.prompts.triage import TRIAGE_SYSTEM_PROMPT as SYSTEM_PROMPT
from src.utils.config import Settings


def _message_content(message):
    """Converte il contenuto di un ToolMessage in un oggetto Python."""
    if isinstance(message.content, str):
        return json.loads(message.content)
    return message.content


def triage_node(
    state: BugFixingState,
    llm: ChatGoogleGenerativeAI,
    settings: Settings,
):
    """Valuta le candidate e sceglie una sola issue."""

    # Recupera i thread eventualmente già richiesti dall'LLM in un passaggio
    # precedente del Triage.
    thread_by_key = {}
    print(f"Attraversamento Nodo Triage con stato")
    
    for message in reversed(state.messages):
        if not isinstance(message, ToolMessage):
            continue
        if message.name != "read_issue_thread":
            continue

        tool_result  = _message_content(message)
        if not isinstance(tool_result, list):
            continue

        for candidate in tool_result:
            if not isinstance(candidate, dict):
                continue
            
            key = str(candidate['repository_full_name']) + '#' + str(candidate['issue_number'])
            thread_by_key[key] = candidate
        break


    enriched_candidates = []
    for candidate in state.issue_candidates:
        enriched_candidate = dict(candidate)
        candidate_key = str(candidate["repository_full_name"]) + "#" + str(candidate["issue_number"])
        if candidate_key in thread_by_key:
            thread = thread_by_key[candidate_key]
            enriched_candidate["body"] = thread.get("body", "")
            enriched_candidate["comments"] = thread.get("comments", [])
            enriched_candidate["issue_state"] = thread.get("state")
        enriched_candidates.append(enriched_candidate)

    candidates_json = json.dumps(
        enriched_candidates,
        ensure_ascii=False,
        indent=2,
    )

    # La richiesta finale è sempre l'ultimo messaggio. Il modello può quindi
    # decidere se emettere una tool_call oppure produrre la selezione finale.
    threads_loaded = bool(thread_by_key)
    if threads_loaded:
        tool_instruction = (
            "I dettagli e i commenti delle candidate sono già presenti. "
            "Non chiamare nuovamente read_issue_thread."
        )
    else:
        tool_instruction = (
            "Prima di selezionare una issue, chiama read_issue_thread una sola "
            "volta passando l'intera lista delle candidate."
        )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"{tool_instruction}\n"
                "Analizza tutte le candidate, scegline al massimo una e "
                "restituisci esclusivamente JSON valido.\n\n"
                f"Candidate:\n{candidates_json}"
            ), 
        ),
    ]

    result = llm.invoke(messages)


    # Se il modello ha richiesto read_issue_thread, il router porterà il
    # messaggio a triage_tools. Non bisogna ancora scegliere l'issue.
    if result.tool_calls:
        return {
            "messages": [result],
            "issue_candidates": enriched_candidates,
            "current_node": "triage",
        }

    if not result.content:
        raise ValueError(
            "Il modello Triage ha restituito una risposta vuota"
        )

    response_text = result.content[0]["text"] if isinstance(result.content, list) else result.content
    data = json.loads(response_text)
    selected_issue = data.get("selected_issue")

    if selected_issue is None:
        return {
            "messages": [result],
            "selected_issue": None,
            "triage_status": "rejected",
            "triage_reason": data.get("reason"),
            "current_node": "triage",
        }

    selected_key = (
        f"{selected_issue['repository_full_name']}#"
        f"{selected_issue['issue_number']}"
    )
    selected_thread = thread_by_key.get(selected_key, {})
    selected_candidate = next(
    (
        candidate
        for candidate in enriched_candidates
        if (
            f"{candidate['repository_full_name']}#"
            f"{candidate['issue_number']}"
        ) == selected_key
    ),
    None,
    )
    if selected_candidate is None:
        raise ValueError(
            "L'LLM ha selezionato una issue non presente nelle candidate"
            )

    repository_stats = selected_candidate.get("repository_stats", {})
    default_branch = repository_stats.get("default_branch")

    return {
        "messages": [result],
        "issue_candidates": enriched_candidates,
        "selected_issue": selected_issue,
        "issue_number": selected_issue["issue_number"],
        "issue_url": selected_issue.get("url"),
        "issue_title": selected_issue.get("title"),
        "repository_full_name": selected_issue[
            "repository_full_name"
        ],
        "repository_stats": repository_stats,
        "default_branch": default_branch,
        "issue_body": selected_thread.get("body"),
        "issue_comments": [
            comment.get("body", "")
            for comment in selected_thread.get("comments", [])
        ],
        "triage_status": data.get("status", "accepted"),
        "triage_reason": data.get("reason"),
        "current_node": "triage",
    }
