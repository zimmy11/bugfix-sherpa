
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import ValidationError
from src.utils.config import Settings
from src.agent.state import BugFixingState
from src.prompts.discovery import DISCOVERY_SYSTEM_PROMPT as SYSTEM_PROMPT
from langchain_core.messages import SystemMessage, ToolMessage
from src.agent.schema import DiscoveryOutcome, IssueCandidate, WorkflowError
import json
from src.agent.errors import WorkflowFailure, invoke_with_retry


def _latest_search_result(messages: list) -> ToolMessage | None:
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == "search_github_issues":
            return message
    return None


def _outcome_update(response, outcome: DiscoveryOutcome) -> dict:
    return {
        "messages": [response] if response is not None else [],
        "issue_candidates": outcome.candidates,
        "discovery_status": outcome.status,
        "status": "failed" if outcome.status == "failed" else "completed" if outcome.status == "no_results" else "running",
        "workflow_error": outcome.error,
        "current_node": "discovery",
    }


def _failed_update(response, reason: str, category: str, *, retryable: bool = False) -> dict:
    return _outcome_update(response, DiscoveryOutcome(
        status="failed",
        error=WorkflowError(
            phase="discovery", message=reason, category=category, retryable=retryable,
        ),
    ))


def discovery_node(
    state: BugFixingState,
    llm: ChatGoogleGenerativeAI,
    settings: Settings,
) -> dict:
    """Accept only validated search results before routing to Triage."""
    try:
        response = invoke_with_retry(
            lambda: llm.invoke([SystemMessage(content=SYSTEM_PROMPT), *state.messages]),
            "discovery", settings,
        )
    except WorkflowFailure as exc:
        return _outcome_update(None, DiscoveryOutcome(status="failed", error=exc.error))

    if getattr(response, "tool_calls", None):
        return {
            "messages": [response],
            "discovery_status": "pending",
            "status": "running",
            "workflow_error": None,
            "current_node": "discovery",
        }

    tool_message = _latest_search_result(state.messages)
    if tool_message is None:
        return _failed_update(response, "Discovery non ha ricevuto risultati dal tool GitHub", category="invariant_violation")

    try:
        result = (
            json.loads(tool_message.content)
            if isinstance(tool_message.content, str)
            else tool_message.content
        )
    except (TypeError, ValueError):
        return _failed_update(response, "Risposta JSON del tool Discovery non valida", category="invariant_violation")

    if not isinstance(result, dict):
        return _failed_update(response, "Risposta del tool Discovery non valida", category="invariant_violation")

    tool_status = result.get("status")
    raw_candidates = result.get("candidates")
    if not isinstance(raw_candidates, list):
        return _failed_update(response, "Candidate Discovery mancanti o non valide", category="invariant_violation")

    candidates: dict[tuple[str, int], IssueCandidate] = {}
    try:
        for raw_candidate in raw_candidates:
            candidate = IssueCandidate.model_validate(raw_candidate)
            key = (candidate.repository_full_name, candidate.issue_number)
            candidates.setdefault(key, candidate)
            if len(candidates) >= settings.github_max_results:
                break
    except ValidationError:
        return _failed_update(response, "Candidata Discovery non valida", category="invariant_violation")

    candidate_list = list(candidates.values())
    error = None
    if tool_status in {"completed", "partial_results"}:
        status = "completed"
    elif tool_status == "no_results":
        status = "no_results"
    elif tool_status in {"rate_limited", "partial_rate_limited"}:
        status = "failed"
        error = WorkflowError(
            phase="discovery", message="Ricerca GitHub interrotta per limite di quota",
            category="operational", retryable=True,
        )
    else:
        return _failed_update(response, "Stato del tool Discovery sconosciuto", "invariant_violation")

    try:
        outcome = DiscoveryOutcome(status=status, candidates=candidate_list, error=error)
    except ValidationError:
        return _failed_update(response, "Esito Discovery incoerente", "invariant_violation")
    return _outcome_update(response, outcome)
