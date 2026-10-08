from src.agent.state import BugFixingState, validate_ingestion_snapshot, validate_selected_issue
from src.utils.config import Settings
from src.agent.schema import DiscoveryOutcome

def route_after_triage(state: BugFixingState) -> str:
    if state.triage_status == "failed":
        return "failed"
    if state.messages:
        last_message = state.messages[-1]
        if getattr(last_message, "tool_calls", None):
            return "tools"

    if state.triage_status == "accepted":
        validate_selected_issue(state)
        return "accepted"

    if state.triage_status == "rejected":
        return "rejected"

    if state.triage_status == "failed":
        return "failed"

    raise ValueError(f"triage_status non valido: {state.triage_status!r}")

def route_after_ingestion(state: BugFixingState, *, settings: Settings) -> str:
    if state.ingestion_status == "failed":
        return "failed"
    if state.messages and getattr(state.messages[-1], "tool_calls", None):
        return "tools"
    if state.ingestion_status == "completed":
        validate_ingestion_snapshot(state, settings=settings)
        return "completed"
    if state.ingestion_status == "failed":
        return "failed"

    raise ValueError(
        f"Ingestion non ha prodotto una transizione valida: "
        f"{state.ingestion_status!r}"
    )

def route_after_discovery(state: BugFixingState) -> str:
    if state.discovery_status == "pending":
        if state.messages and getattr(state.messages[-1], "tool_calls", None):
            return "tools"
        raise ValueError("Discovery non ha prodotto un esito valido")

    # Later phases may have their own error in the shared State. Only the
    # Discovery error belongs to this outcome.
    error = state.workflow_error
    discovery_error = error if error is not None and error.phase == "discovery" else None
    outcome = DiscoveryOutcome(
        status=state.discovery_status,
        candidates=state.issue_candidates,
        error=discovery_error,
    )
    return outcome.status
