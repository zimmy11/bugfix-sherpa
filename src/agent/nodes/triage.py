import json
from typing import Literal

from pydantic import ValidationError
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from src.agent.schema import TriageDecision, WorkflowError
from src.agent.state import BugFixingState
from src.prompts.triage import TRIAGE_SYSTEM_PROMPT as SYSTEM_PROMPT
from src.utils.config import Settings
from src.agent.errors import WorkflowFailure, invoke_with_retry


def _message_content(message):
    """Converte il contenuto di un ToolMessage in un oggetto Python."""
    if isinstance(message.content, str):
        return json.loads(message.content)
    return message.content

def _update_failed(
    response: BaseMessage | None,
    reason: str,
    category: Literal["operational", "invalid_input", "invariant_violation"],
    *,
    retryable: bool = False,
) -> dict:
    """Preserve candidates and clear stale selection on a failed attempt."""
    return {
        "messages": [response] if response is not None else [],
        "selected_issue": None,
        "issue_number": None,
        "issue_url": None,
        "issue_title": None,
        "issue_body": None,
        "issue_comments": [],
        "issue_timeline_events": [],
        "issue_work_claim_signals": [],
        "repository_full_name": None,
        "repository_stats": None,
        "default_branch": None,
        "triage_reason": None,
        "triage_status": "failed",
        "status": "failed",
        "workflow_error": WorkflowError(
            phase="triage", message=reason, category=category, retryable=retryable,
        ),
        "current_node": "triage",
    }


def triage_node(
    state: BugFixingState,
    llm: ChatGoogleGenerativeAI,
    settings: Settings,
):
    """Valuta le candidate e sceglie una sola issue."""

    # Recupera i thread eventualmente già richiesti dall'LLM in un passaggio
    # precedente del Triage.
    thread_by_key = {}
    print("Attraversamento Nodo Triage con stato")
    
    for message in reversed(state.messages):
        if not isinstance(message, ToolMessage):
            continue
        if message.name != "read_issue_thread":
            continue

        try:
            tool_result = _message_content(message)
        except (TypeError, ValueError):
            return _update_failed(None, "JSON del tool Triage non valido", "invariant_violation")
        if not isinstance(tool_result, list):
            return _update_failed(None, "Risposta del tool Triage non valida", "invariant_violation")

        for candidate in tool_result:
            if not isinstance(candidate, dict):
                return _update_failed(None, "Thread Triage non valido", "invariant_violation")

            repository_full_name = candidate.get("repository_full_name")
            issue_number = candidate.get("issue_number")
            if (
                not isinstance(repository_full_name, str) or not repository_full_name.strip()
                or not isinstance(issue_number, int) or isinstance(issue_number, bool)
                or issue_number <= 0
            ):
                return _update_failed(None, "Identita del thread Triage non valida", "invariant_violation")

            key = f"{repository_full_name}#{issue_number}"
            thread_by_key[key] = candidate
        break


    enriched_candidates = []
    for candidate in state.issue_candidates:
        enriched_candidate = candidate.model_dump(mode="json")
        candidate_key = f"{candidate.repository_full_name}#{candidate.issue_number}"
        if candidate_key in thread_by_key:
            thread = thread_by_key[candidate_key]
            enriched_candidate["body"] = thread.get("body", "")
            enriched_candidate["comments"] = thread.get("comments", [])
            enriched_candidate["timeline_events"] = thread.get(
                "timeline_events", []
            )
            enriched_candidate["work_claim_signals"] = thread.get(
                "work_claim_signals", []
            )
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

    try:
        result = invoke_with_retry(lambda: llm.invoke(messages), "triage", settings)
    except WorkflowFailure as exc:
        return _update_failed(None, exc.error.message, exc.error.category, retryable=exc.error.retryable)


    # Se il modello ha richiesto read_issue_thread, il router porterà il
    # messaggio a triage_tools. Non bisogna ancora scegliere l'issue.
    if result.tool_calls:
        return {
            "messages": [result],
            "current_node": "triage",
            "triage_status": "pending",
            "status": "running",
            "workflow_error": None,
        }

    if not result.content:
        return _update_failed(response=result, reason="Il modello Triage ha restituito una risposta vuota", category="invariant_violation"
        )

    try:
        response_text = result.content[0]["text"] if isinstance(result.content, list) else result.content
        decision = TriageDecision.model_validate_json(response_text)
    except (ValidationError, KeyError, IndexError, TypeError):
        return _update_failed(result, "Decisione del modello Triage non valida", "invariant_violation")

    if decision.status == "rejected":
        return {
            "messages": [result],
            "selected_issue": None,
            "issue_number": None,
            "issue_url": None,
            "issue_title": None,
            "issue_body": None,
            "issue_comments": [],
            "issue_timeline_events": [],
            "issue_work_claim_signals": [],
            "repository_full_name": None,
            "repository_stats": None,
            "default_branch": None,
            "triage_status": decision.status,
            "status": "completed",
            "triage_reason": decision.reason,
            "current_node": "triage",
            "workflow_error": None,
        }

    selected_candidate = next(
        (
            candidate
            for candidate in state.issue_candidates
            if (
                f"{candidate.repository_full_name}#{candidate.issue_number}"
            ) == decision.issue_key
        ),
        None,
    )

    if selected_candidate is None:
        return _update_failed(result,reason="L'LLM ha selezionato una issue non presente nelle candidate", category="invariant_violation"
        )

    selected_thread = thread_by_key.get(decision.issue_key)

    if selected_thread is None:
        return _update_failed(result, "Thread della issue selezionata mancante", "invariant_violation")
    if selected_thread.get("error"):
        if selected_thread.get("workflow_error") is not None:
            try:
                error = WorkflowError.model_validate(selected_thread["workflow_error"])
            except ValidationError:
                return _update_failed(result, "Errore GitHub strutturato non valido", "invariant_violation")
            if error.phase != "triage":
                return _update_failed(result, "Errore GitHub associato alla fase errata", "invariant_violation")
            return _update_failed(result, error.message, error.category, retryable=error.retryable)
        # Compatibility with older HTTP-only payloads during migration.
        code = selected_thread.get("http_status")
        if not isinstance(code, int) or isinstance(code, bool):
            return _update_failed(result, "Errore del thread GitHub senza metadati HTTP", "invariant_violation")
        invalid_input = code in {400, 422}
        return _update_failed(
            result,
            "Parametri del thread GitHub non validi" if invalid_input else "Impossibile leggere il thread GitHub",
            "invalid_input" if invalid_input else "operational",
            retryable=not invalid_input and selected_thread.get("retryable") is True,
        )

    return {
        "messages": [result],
        "selected_issue": selected_candidate,
        "issue_number": selected_candidate.issue_number,
        "issue_url": selected_candidate.url,
        "issue_title": selected_candidate.title,
        "repository_full_name": selected_candidate.repository_full_name,
        "repository_stats": selected_candidate.repository_stats,
        "default_branch": selected_candidate.repository_stats.default_branch,
        "issue_body": selected_thread.get("body", ""),
        "issue_comments": [
            comment.get("body", "")
            for comment in selected_thread.get("comments", [])
        ],
        "issue_timeline_events": selected_thread.get("timeline_events", []),
        "issue_work_claim_signals": selected_thread.get(
            "work_claim_signals", []
        ),
        "triage_status": decision.status,
        "status": "running",
        "triage_reason": decision.reason,
        "current_node": "triage",
        "workflow_error": None
    }