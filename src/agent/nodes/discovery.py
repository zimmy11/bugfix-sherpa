
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import ValidationError
from src.utils.config import Settings
from src.agent.state import BugFixingState
from src.prompts.discovery import DISCOVERY_SYSTEM_PROMPT as SYSTEM_PROMPT
from langchain_core.messages import SystemMessage, ToolMessage
from src.agent.schema import DiscoveryOutcome, IssueCandidate, WorkflowError, SearchIssuesResult
import json
from src.agent.errors import WorkflowFailure, invoke_with_retry


def _latest_search_result(messages: list) -> ToolMessage | None:
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == "search_github_issues":
            return message
    return None


def _outcome_update(response, outcome: DiscoveryOutcome, error: WorkflowError | None = None) -> dict:
    return {
        "messages": [response] if response is not None else [],
        "discovery_outcome": outcome,
        "status": "failed" if outcome.status == "failed" else "completed" if outcome.status == "no_results" else "running",
        "current_node": "discovery",
        "workflow_error": error,
    }


def _failed_update(response, reason: str, category: str, candidates: list[IssueCandidate] | None = None, retryable: bool = False) -> dict:
    return _outcome_update(response, DiscoveryOutcome(
        status="failed",
        candidates=candidates if candidates else [], ),
        error=WorkflowError(
            phase="discovery", message=reason, category=category, retryable=retryable,
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
        return _outcome_update(None, DiscoveryOutcome(status="failed"), error=exc.error)

    if getattr(response, "tool_calls", None):
        return {
            "messages": [response],
            "discovery_outcome": DiscoveryOutcome(status="pending"),
            "status": "running",
        }
    

    tool_message = _latest_search_result(state.messages)

    if tool_message is None:
        return _failed_update(response, "Discovery non ha ricevuto risultati dal tool GitHub", category="invariant_violation")

    try:
        raw = json.loads(tool_message.content)
        tool_result = SearchIssuesResult.model_validate(raw)

    except (TypeError, ValueError):
        return _failed_update(response, "Risposta JSON del tool Discovery non valida", category="invariant_violation")

    tool_status = tool_result.status
    raw_candidates = tool_result.candidates

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
        outcome = DiscoveryOutcome(
            status=status,
            candidates=candidate_list,
            message=tool_result.message,
        )
    except ValidationError:
        return _failed_update(response, "Esito Discovery incoerente", "invariant_violation")
    return _outcome_update(response, outcome, error=error)
