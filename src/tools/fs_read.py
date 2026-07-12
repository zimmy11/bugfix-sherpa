"""
This module provides a tool for reading the contents of a file from the local filesystem. It defines a function `fs_read_file` that takes a file path as input and returns the contents of the file as a string. The function is decorated with `@tool` to indicate that it is a tool that can be used in the context of LangChain.
The `fs_read_file` function opens the specified file in read mode, reads its contents, and returns the contents as a string. If the file does not exist or cannot be read, an exception will be raised.
This tool can be used in various applications where reading file contents is required, such as processing configuration files.
"""
from langchain_core.tools import tool

@tool
def fs_read_file(file_path: str) -> str:
    """Read a text file needed to understand the cloned repository.

    Use this tool during ingestion to inspect small project documents such as
    README, CONTRIBUTING and dependency manifests. Supply a file path inside
    the configured repository workspace. Do not use it for binary files,
    secrets, files outside the workspace or very large source files that
    should instead be read in bounded chunks.

    Args:
        file_path: Path of the UTF-8 text file to read, restricted to the
            current repository workspace.

    Returns:
        The complete text content of the requested file. File-system and
        decoding failures should be converted to a concise explanatory error
        result by the final hardened implementation.
    """
    with open(file_path, "r") as f:
        return f.read()
