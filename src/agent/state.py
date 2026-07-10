from __future__ import annotations
from langgraph.graph import add_messages
from pydantic import BaseModel, Field
from typing import Annotated, List, Optional

class BugFixingState(BaseModel):
    '''
    State class for the bug fixing process. It holds the current state of the bug fixing process, including the current step, the identified bug location, and the suggested fix.
    '''
    messages: Annotated[List[dict], add_messages] = Field(default_factory=list, description="List of messages exchanged during the bug fixing process.")
    local_repo_path: Optional[str] = Field(default=None, description="The local path to the cloned GitHub repository.")
    repository_name: Optional[str] = Field(default=None, description="The name of the GitHub repository being analyzed.")
    issue_context: Optional[str] = Field(default=None, description="The context of the issue being fixed.")
    issue_number: Optional[int] = Field(default=None, description="The number of the issue being fixed.")
    human_feedback: Optional[str] = Field(default=None, description="Feedback provided by a human during the bug fixing process.")
    test_logs: Optional[str] = Field(default=None, description="Logs from running tests on the codebase.")
    files_analyzed: Optional[List[str]] = Field(default=None, description="List of files that have been analyzed during the bug fixing process.")
    current_hypothesis: Optional[str] = Field(default=None, description="The current hypothesis about the bug and its fix.")