from src.agent.state import BugFixingState, validate_ingestion_snapshot, validate_selected_issue

def route_after_triage(state: BugFixingState) -> str:
    if state.messages:
        last_message = state.messages[-1]
        if getattr(last_message, "tool_calls", None):
            return "tools"
    
    if state.triage_status == "accepted":
        validate_selected_issue(state)
        return "accepted" 
    
    if state.triage_status == "rejected":
            return "rejected" 
        
    raise ValueError(f"triage_status non valido: {state.triage_status!r}")

def route_after_ingestion(state: BugFixingState) -> str:
    if state.messages and getattr(state.messages[-1], "tool_calls", None):
        return "tools"
    if state.ingestion_status == "completed":
        validate_ingestion_snapshot(state)
        return "completed"
    if state.ingestion_status == "failed":
        return "failed"
    
    raise ValueError(
        f"Ingestion non ha prodotto una transizione valida: "
        f"{state.ingestion_status!r}"
    )

def route_after_discovery(state: BugFixingState) -> str:
    if not state.messages:
        return "completed"

    last_message = state.messages[-1]

    if getattr(last_message, "tool_calls", None):
        return "tools"

    return "completed"
