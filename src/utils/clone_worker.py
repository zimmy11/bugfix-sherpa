from __future__ import annotations
from multiprocessing.context import Pipe, Process, spawn, Event, join, is_alive, exitcode

from git import Repo
from src.utils.repository import RepositoryValidationError


_EXCLUDED_DIRECTORIES = [".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", "node_modules", "build", "dist", "coverage"]
_SENSITIVE_FILE_PATTERNS = [".env", ".env.*", "*.pem", "*.key", "id_rsa*", "credentials*", "secrets*"]
_GUIDE_CANDIDATES = ["README*", "CONTRIBUTING*", "DEVELOPMENT*", "TESTING*", ".github/CONTRIBUTING*"]
_MANIFEST_CANDIDATES = ["pyproject.toml", "setup.py", "setup.cfg", "requirements*.txt","Pipfile", "Pipfile.lock"," poetry.lock", "uv.lock", "tox.ini", "pytest.ini", ".python-version"]

