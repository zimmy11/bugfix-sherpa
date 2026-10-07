
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import ValidationError
from src.utils.config import Settings
from src.agent.state import BugFixingState
from src.prompts.discovery import DISCOVERY_SYSTEM_PROMPT as SYSTEM_PROMPT
from langchain_core.messages import SystemMessage, ToolMessage
from src.agent.schema import IssueCandidate
import json 


def _latest_search_result(messages: list) -> ToolMessage | None:
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == "search_github_issues":
            return message
    return None


def _failed_update(response, reason: str) -> dict:
    return {
        "messages": [response],
        "issue_candidates": [],
        "discovery_status": "failed",
        "discovery_error": reason,
        "current_node": "discovery",
    }


def discovery_node(
    state: BugFixingState,
    llm: ChatGoogleGenerativeAI,
    settings: Settings,
) -> dict:
    """Accept only validated search results before routing to Triage."""
    response = llm.invoke([SystemMessage(content=SYSTEM_PROMPT), *state.messages])

    if getattr(response, "tool_calls", None):
        return {
            "messages": [response],
            "discovery_status": "pending",
            "discovery_error": None,
            "current_node": "discovery",
        }

    tool_message = _latest_search_result(state.messages)
    if tool_message is None:
        return _failed_update(response, "Discovery non ha ricevuto risultati dal tool GitHub")

    try:
        result = (
            json.loads(tool_message.content)
            if isinstance(tool_message.content, str)
            else tool_message.content
        )
    except (TypeError, ValueError):
        return _failed_update(response, "Risposta JSON del tool Discovery non valida")

    if not isinstance(result, dict):
        return _failed_update(response, "Risposta del tool Discovery non valida")

    tool_status = result.get("status")
    raw_candidates = result.get("candidates")
    if not isinstance(raw_candidates, list):
        return _failed_update(response, "Candidate Discovery mancanti o non valide")

    candidates: dict[tuple[str, int], IssueCandidate] = {}
    try:
        for raw_candidate in raw_candidates:
            candidate = IssueCandidate.model_validate(raw_candidate)
            key = (candidate.repository_full_name, candidate.issue_number)
            candidates.setdefault(key, candidate)
            if len(candidates) >= settings.github_max_results:
                break
    except ValidationError:
        return _failed_update(response, "Candidata Discovery non valida")

    candidate_list = list(candidates.values())
    update = {
        "messages": [response],
        "issue_candidates": candidate_list,
        "discovery_error": None,
        "current_node": "discovery",
    }

    if tool_status in {"completed", "partial_results"}:
        if not candidate_list:
            return _failed_update(response, "Ricerca completata senza candidate")
        update["discovery_status"] = "completed"
    elif tool_status == "no_results":
        if candidate_list:
            return _failed_update(response, "Risultato Discovery incoerente")
        update["discovery_status"] = "no_results"
    elif tool_status in {"rate_limited", "partial_rate_limited"}:
        update["discovery_status"] = "failed"
        message = result.get("message")
        update["discovery_error"] = (
            message.strip()[:500]
            if isinstance(message, str) and message.strip()
            else "Ricerca GitHub interrotta per limite di quota"
        )
    else:
        return _failed_update(response, "Stato del tool Discovery sconosciuto")

    return update
