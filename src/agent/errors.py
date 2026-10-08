"""Safe workflow boundaries and bounded retries for individual operations."""
from __future__ import annotations

from collections.abc import Callable
from time import sleep, time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import requests
from github.GithubException import GithubException
from google.genai.errors import APIError
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import ValidationError

from src.agent.schema import WorkflowError, WorkflowPhase
from src.utils.config import Settings
from src.utils.repository_safety import RepositoryValidationError


class WorkflowFailure(Exception):
    """Carry a validated diagnostic without exposing the original exception."""
    def __init__(self, error: WorkflowError):
        self.error = error
        super().__init__(error.message)


def github_rate_limited(error: GithubException) -> bool:
    headers = {str(k).lower(): str(v) for k, v in (error.headers or {}).items()}
    data = error.data if isinstance(error.data, dict) else {}
    message = data.get("message", "")
    return error.status == 429 or (error.status == 403 and (
        headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers
        or (isinstance(message, str) and "rate limit" in message.lower())
    ))


def classify_exception(error: Exception, phase: WorkflowPhase) -> WorkflowError:
    if isinstance(error, WorkflowFailure):
        return error.error
    category = "invariant_violation"
    retryable = False
    message = "Violazione del contratto del workflow"
    if isinstance(error, (TimeoutError, ConnectionError, httpx.TransportError,
                          requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        category, retryable, message = "operational", True, "Connessione al servizio interrotta"
    elif isinstance(error, (GithubException, APIError, ChatGoogleGenerativeAIError,
                            httpx.HTTPStatusError, requests.exceptions.HTTPError)):
        cause = error.__cause__ if isinstance(error, ChatGoogleGenerativeAIError) else error
        if isinstance(cause, GithubException):
            code = cause.status
            limited = github_rate_limited(cause)
        elif isinstance(cause, APIError):
            code, limited = cause.code, cause.code == 429
        else:
            response = getattr(cause, "response", None)
            code = getattr(response, "status_code", None)
            limited = code == 429
        category = "invalid_input" if code in {400, 422} else "operational"
        retryable = limited or (isinstance(code, int) and (code == 408 or 500 <= code < 600))
        message = "Richiesta al servizio non valida" if category == "invalid_input" else "Servizio esterno non disponibile"
    elif isinstance(error, RepositoryValidationError):
        pass
    elif isinstance(error, (ValidationError, ValueError, TypeError)):
        category, message = "invalid_input", "Input del workflow non valido"
    elif isinstance(error, OSError):
        category, message = "operational", "Operazione di sistema non riuscita"
    return WorkflowError(phase=phase, category=category, message=message, retryable=retryable)


def _required_wait(exc: Exception) -> float:
    cause = exc.__cause__ if isinstance(exc, ChatGoogleGenerativeAIError) else exc
    raw_headers = getattr(cause, "headers", None)
    if raw_headers is None:
        raw_headers = getattr(getattr(cause, "response", None), "headers", {})
    headers = {str(k).lower(): str(v) for k, v in (raw_headers or {}).items()}
    try:
        if "retry-after" in headers:
            value = headers["retry-after"]
            try:
                return max(0, float(value))
            except ValueError:
                return max(0, parsedate_to_datetime(value).timestamp() - time())
        if headers.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in headers:
            return max(0, float(headers["x-ratelimit-reset"]) - time())
    except (ValueError, TypeError, OverflowError):
        return float("inf")  # Do not guess when an explicit wait is malformed.
    return 0


def invoke_with_retry(operation: Callable[[], Any], phase: WorkflowPhase,
                      settings: Settings, *, allow_retry: bool = True) -> Any:
    """Retry only confirmed transient exceptions, never successful batch peers."""
    for attempt in range(settings.api_max_retries):
        try:
            return operation()
        except Exception as exc:
            error = classify_exception(exc, phase)
            if (not allow_retry or error.category != "operational" or not error.retryable
                    or attempt + 1 == settings.api_max_retries):
                raise WorkflowFailure(error) from None
            required_wait = _required_wait(exc)
            if required_wait > settings.api_retry_max_seconds:
                raise WorkflowFailure(error) from None
            delay = max(required_wait, min(settings.api_retry_min_seconds * (2 ** attempt), settings.api_retry_max_seconds))
            sleep(delay)
    raise AssertionError("Settings require at least one attempt")


def failure_update(error: WorkflowError) -> dict[str, Any]:
    """Keep legacy text mirrors until all consumers have migrated."""
    update = {"workflow_error": error, "status": "failed", "current_node": error.phase,
              "error_message": error.message}
    if error.phase in {"discovery", "triage", "ingestion"}:
        update[f"{error.phase}_status"] = "failed"
    if error.phase == "triage":
        update.update(selected_issue=None, issue_number=None, issue_url=None,
                      repository_full_name=None, repository_stats=None, default_branch=None)
    if error.phase == "ingestion":
        update["ingestion_error"] = error.message
    return update


def phase_boundary(node: Callable, phase: WorkflowPhase, *, accepts_config: bool = False, settings: Settings | None = None) -> Callable:
    """Stop on exceptions, unimplemented nodes or invalid State updates."""
    def run(state, config: RunnableConfig = None):
        from src.agent.state import BugFixingState, validate_selected_issue, validate_ingestion_snapshot
        try:
            update = node(state, config=config) if accepts_config else node(state)
            if not isinstance(update, dict):
                return failure_update(WorkflowError(
                    phase=phase, category="invariant_violation",
                    message="La fase non ha prodotto un aggiornamento valido",
                ))
            error = update.get("workflow_error")
            if error is not None:
                try:
                    error = WorkflowError.model_validate(error)
                except ValidationError:
                    raise WorkflowFailure(WorkflowError(phase=phase, category="invariant_violation", message="Errore strutturato non valido")) from None
                if error.phase != phase:
                    raise WorkflowFailure(WorkflowError(phase=phase, category="invariant_violation", message="Errore associato alla fase errata"))
                update = {**update, **failure_update(error)}
            elif update.get("status") == "failed" or update.get(f"{phase}_status") == "failed":
                raise WorkflowFailure(WorkflowError(phase=phase, category="invariant_violation", message="Fallimento privo di errore strutturato"))
            else:
                update.setdefault("workflow_error", None)
                update.setdefault("error_message", None)
                if phase == "ingestion":
                    update.setdefault("ingestion_error", None)
            try:
                assembled = BugFixingState.model_validate({**state.model_dump(), **update})
                if phase == "triage" and assembled.triage_status == "accepted":
                    validate_selected_issue(assembled)
                if phase == "ingestion" and assembled.ingestion_status == "completed":
                    if settings is None:
                        raise ValueError("Missing ingestion settings")
                    validate_ingestion_snapshot(assembled, settings)
            except (ValidationError, ValueError):
                return failure_update(WorkflowError(
                    phase=phase, category="invariant_violation", message="Aggiornamento dello State incoerente",
                ))
            return update
        except WorkflowFailure as exc:
            return failure_update(exc.error)
        except Exception as exc:
            return failure_update(classify_exception(exc, phase))
    return run


def guarded_tool(tool: BaseTool, phase: WorkflowPhase, settings: Settings) -> BaseTool:
    """Retry each read operation separately; clone operations are never replayed."""
    def invoke(config: RunnableConfig, **kwargs):
        def operation():
            value = tool.invoke(kwargs, config=config)
            if isinstance(value, dict) and value.get("status") == "failed":
                error = value.get("error")
                if isinstance(error, dict):
                    try:
                        validated = WorkflowError.model_validate(error)
                    except ValidationError:
                        raise WorkflowFailure(WorkflowError(phase=phase, category="invariant_violation", message="Errore del tool non valido")) from None
                    raise WorkflowFailure(validated)
                raise WorkflowFailure(WorkflowError(
                    phase=phase, category="invariant_violation", message="Tool fallito senza errore strutturato",
                ))
            return value
        return invoke_with_retry(operation, phase, settings,
                                 allow_retry=tool.name in {"search_github_issues", "read_issue_thread"})
    return StructuredTool.from_function(
        func=invoke, name=tool.name, description=tool.description,
        args_schema=tool.args_schema, infer_schema=False,
    )
