
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

def _outcome_update(response, outcome: DiscoveryOutcome,  attempt: int,  error: WorkflowError | None = None) -> dict:
    return {
        "messages": [response] if response is not None else [],
        "discovery_outcome": outcome,
        "status": "failed" if outcome.status in {"failed", "rate_limited", "partial_rate_limited"} else "completed" if outcome.status == "no_results" else "running",
        "current_node": "discovery",
        "workflow_error": error,
        "discovery_attempt": attempt
    }


def _failed_update(response, reason: str, category: str, attempt: int,  candidates: list[IssueCandidate] | None = None, retryable: bool = False) -> dict:
    return _outcome_update(response, DiscoveryOutcome(
        status="failed",
        candidates=candidates if candidates else [], ),
        error=WorkflowError(
            phase="discovery", message=reason, category=category, retryable=retryable,
        ), attempt = attempt)


def discovery_node(
    state: BugFixingState,
    llm: ChatGoogleGenerativeAI,
    settings: Settings,
) -> dict:
    """Apply the model's final selection to the current search result."""
    attempt = state.discovery_attempt
    if (state.messages and isinstance(state.messages[-1], ToolMessage)
            and state.messages[-1].name == "search_github_issues"):
        try:
            current_result = get_result_for_current_search(state.messages)
        except (TypeError, ValueError):
            return _failed_update(None, "Risposta JSON del tool Discovery non valida", "invariant_violation", attempt)
        if current_result.status in {"rate_limited", "partial_rate_limited"}:
            return _outcome_update(None, DiscoveryOutcome(
                status=current_result.status,
                candidates=current_result.candidates,
                message=current_result.message,
            ), attempt=attempt, error=WorkflowError(
                phase="discovery", message="Ricerca GitHub interrotta per limite di quota",
                category="operational", retryable=True,
            ))
    if attempt >= settings.discovery_max_steps:
        return _failed_update(None, "Discovery ha raggiunto il numero massimo di passaggi", category = "operational", attempt = attempt)
    attempt += 1

    try:
        response = invoke_with_retry(
            lambda: llm.invoke([SystemMessage(content=SYSTEM_PROMPT), *state.messages]),
            "discovery", settings,
        )
    except WorkflowFailure as exc:
        return _outcome_update(None, DiscoveryOutcome(status="failed"), error=exc.error, attempt=attempt)

    if getattr(response, "tool_calls", None):
        if (len(response.tool_calls) != 1
                or response.tool_calls[0]["name"] != "search_github_issues"):
            return _failed_update(response, "Chiamata tool Discovery non valida", "invariant_violation", attempt=attempt)
        if any(
            isinstance(message, AIMessage)
            and any(call["name"] == "search_github_issues" for call in message.tool_calls)
            for message in state.messages
        ):
            return _failed_update(response, "Ricerca GitHub ripetuta in Discovery", "invariant_violation", attempt=attempt)
        return {
            "messages": [response],
            "discovery_outcome": DiscoveryOutcome(status="pending"),
            "workflow_error": None,
            "status": "running",
            "current_node": "discovery",
            "discovery_attempt": attempt
        }

    try:
        tool_result = get_result_for_current_search(state.messages)
    except (TypeError, ValueError):
        return _failed_update(response, "Risposta JSON del tool Discovery non valida", category="invariant_violation",attempt=attempt)

    tool_status = tool_result.status
    if tool_status in {"rate_limited", "partial_rate_limited"}:
        error = WorkflowError(
            phase="discovery", message="Ricerca GitHub interrotta per limite di quota",
            category="operational", retryable=True,
        )
        return _outcome_update(response, DiscoveryOutcome(
            status=tool_status, candidates=tool_result.candidates,
            message=tool_result.message,
        ), error=error, attempt=attempt)
    if tool_status == "no_results":
        return _outcome_update(response, DiscoveryOutcome(
            status="no_results", message=tool_result.message,
        ), attempt=attempt)

    try:
        selection = DiscoverySelection.model_validate_json(response.content)
    except (TypeError, ValueError):
        return _failed_update(response, "Classificazione finale Discovery non valida", "invariant_violation",attempt=attempt)

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
        return _failed_update(response, "Esito Discovery incoerente", "invariant_violation", attempt=attempt)
    return _outcome_update(response, outcome, attempt=attempt)
