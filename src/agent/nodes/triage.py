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


def _response_text(content) -> str:
    """Normalizza il contenuto della risposta Gemini in testo JSON."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(
                block.get("text"), str
            ):
                parts.append(block["text"])
        return "".join(parts)

    raise TypeError(
        f"Formato risposta LLM non supportato: {type(content).__name__}"
    )


def triage_node(
    state: BugFixingState,
    llm: ChatGoogleGenerativeAI,
    settings: Settings,
):
    """Valuta le candidate e sceglie una sola issue."""

    # Recupera i thread eventualmente già richiesti dall'LLM in un passaggio
    # precedente del Triage.
    thread_by_key = {}
    print(f"Attraversamento Nodo Triage con stato: {state}")

    for message in state.messages:
        if not isinstance(message, ToolMessage):
            continue
        if message.name != "read_issue_thread":
            continue

        try:
            thread = _message_content(message)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

        if not isinstance(thread, dict):
            continue
        if "repository_full_name" not in thread:
            continue
        if "issue_number" not in thread:
            continue

        key = (
            f"{thread['repository_full_name']}#{thread['issue_number']}"
        )
        thread_by_key[key] = thread

    issue_contexts = dict(state.issue_contexts)
    issue_contexts.update(thread_by_key)

    enriched_candidates = []
    for candidate in state.issue_candidates:
        key = (
            f"{candidate['repository_full_name']}#"
            f"{candidate['issue_number']}"
        )
        enriched_candidate = dict(candidate)

        if key in thread_by_key:
            thread = thread_by_key[key]
            enriched_candidate["full_body"] = thread.get("body", "")
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
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=(
                "Analizza le candidate qui sotto. Usa read_issue_thread "
                "solo se le informazioni disponibili non sono sufficienti "
                "per valutare una candidate. Dopo gli eventuali tool, "
                "scegli una sola issue e restituisci esclusivamente JSON "
                "valido.\n\n"
                f"Candidate:\n{candidates_json}"
            )
        ),
    ]

    result = llm.invoke(messages)


    # Se il modello ha richiesto read_issue_thread, il router porterà il
    # messaggio a triage_tools. Non bisogna ancora scegliere l'issue.
    if result.tool_calls:
        return {
            "messages": [result],
            "issue_contexts": issue_contexts,
            "current_node": "triage",
        }

    if not result.content:
        raise ValueError(
            "Il modello Triage ha restituito una risposta vuota"
        )

    response_text = _response_text(result.content).strip()
    data = json.loads(response_text)
    selected_issue = data.get("selected_issue")

    if selected_issue is None:
        return {
            "messages": [result],
            "issue_contexts": issue_contexts,
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

    return {
        "messages": [result],
        "issue_contexts": issue_contexts,
        "selected_issue": selected_issue,
        "issue_number": selected_issue["issue_number"],
        "issue_url": selected_issue.get("url"),
        "issue_title": selected_issue.get("title"),
        "repository_full_name": selected_issue[
            "repository_full_name"
        ],
        "issue_context": selected_thread.get("body"),
        "issue_comments": [
            comment.get("body", "")
            for comment in selected_thread.get("comments", [])
        ],
        "triage_status": data.get("status", "accepted"),
        "triage_reason": data.get("reason"),
        "current_node": "triage",
    }
