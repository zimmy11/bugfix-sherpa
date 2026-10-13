
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import ValidationError
from src.utils.config import Settings
from src.agent.state import BugFixingState
from src.prompts.discovery import DISCOVERY_SYSTEM_PROMPT as SYSTEM_PROMPT
from langchain_core.messages import SystemMessage, ToolMessage, AIMessage
from src.agent.schema import DiscoveryOutcome, IssueCandidate, WorkflowError, SearchIssuesResult, DiscoverySelection
from src.agent.errors import WorkflowFailure, invoke_with_retry


def get_result_for_current_search(messages: list) -> SearchIssuesResult:
    for call_index in range(len(messages) - 1, -1, -1):
        message = messages[call_index]
        if not isinstance(message, AIMessage):
            continue
        search_calls = [call for call in message.tool_calls if call["name"] == "search_github_issues"]
        if not search_calls:
            continue
        if len(search_calls) != 1:
            raise ValueError("Discovery richiede una sola ricerca per passaggio")
        call_id = search_calls[0]["id"]
        for result in messages[call_index + 1:]:
            if (isinstance(result, ToolMessage)
                    and result.name == "search_github_issues"
                    and result.tool_call_id == call_id):
                return SearchIssuesResult.model_validate_json(result.content)
        raise ValueError("Risultato della ricerca corrente mancante")

    raise ValueError("Nessuna ricerca Discovery trovata")

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
    """Apply the model's final selection to the current search result."""
    try:
        response = invoke_with_retry(
            lambda: llm.invoke([SystemMessage(content=SYSTEM_PROMPT), *state.messages]),
            "discovery", settings,
        )
    except WorkflowFailure as exc:
        return _outcome_update(None, DiscoveryOutcome(status="failed"), error=exc.error)

    if getattr(response, "tool_calls", None):
        if (len(response.tool_calls) != 1
                or response.tool_calls[0]["name"] != "search_github_issues"):
            return _failed_update(response, "Chiamata tool Discovery non valida", "invariant_violation")
        return {
            "messages": [response],
            "discovery_outcome": DiscoveryOutcome(status="pending"),
            "workflow_error": None,
            "status": "running",
            "current_node": "discovery",
        }

    try:
        tool_result = get_result_for_current_search(state.messages)
    except (TypeError, ValueError):
        return _failed_update(response, "Risposta JSON del tool Discovery non valida", category="invariant_violation")

    tool_status = tool_result.status
    if tool_status in {"rate_limited", "partial_rate_limited"}:
        error = WorkflowError(
            phase="discovery", message="Ricerca GitHub interrotta per limite di quota",
            category="operational", retryable=True,
        )
        return _outcome_update(response, DiscoveryOutcome(
            status="failed", candidates=tool_result.candidates,
            message=tool_result.message,
        ), error=error)
    if tool_status == "no_results":
        return _outcome_update(response, DiscoveryOutcome(
            status="no_results", message=tool_result.message,
        ))

    try:
        selection = DiscoverySelection.model_validate_json(response.content)
    except (TypeError, ValueError):
        return _failed_update(response, "Classificazione finale Discovery non valida", "invariant_violation")

    available: dict[tuple[str, int], IssueCandidate] = {}
    for candidate in tool_result.candidates:
        available.setdefault(
            (candidate.repository_full_name, candidate.issue_number), candidate,
        )
    selected: list[IssueCandidate] = []
    seen: set[tuple[str, int]] = set()
    for key in selection.candidates:
        identity = (key.repository_full_name, key.issue_number)
        if identity not in available or identity in seen:
            continue
        selected.append(available[identity])
        seen.add(identity)
        if len(selected) >= settings.github_max_results:
            break

    try:
        outcome = DiscoveryOutcome(
            status="completed" if selected else "no_results",
            candidates=selected,
            message=selection.message,
        )
    except ValidationError:
        return _failed_update(response, "Esito Discovery incoerente", "invariant_violation")
    return _outcome_update(response, outcome)
