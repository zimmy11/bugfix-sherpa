from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

class RepositoryValidationError(ValueError):
    """Errore relativo alla validazione di un repository locale."""


class GitCommandError(RuntimeError):
    """Errore durante l'esecuzione di un comando Git."""



_SCP_REMOTE_PATTERN = re.compile(
    r"^(?:(?P<user>[^@/:\\\s]+)@)?"
    r"(?P<host>\[[^\]]+\]|[^:/\\\s]+):"
    r"(?P<path>.+)$"
)

_WINDOWS_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
_EXCLUDED_CHARS = ["..", "//", "@{", "\\","~" ,"^", ":", "?", "*", "["]
_NO_FINAL_CHARS = [".", "/", " "]
_CONTROL_CHARS = ["\n", "\r", "\n", "\x00", "\b", "\x1b"]

def _run_git(repository: Path, *args: str) -> str:
    """
    Esegue un comando Git senza usare la shell.

    Restituisce stdout senza spazi iniziali o finali.
    Solleva GitCommandError in caso di errore.
    """
    command = ["git", "-C", str(repository), *args]

    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"

    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
    except FileNotFoundError as exc:
        raise GitCommandError(
            "Git non è installato oppure non è disponibile nel PATH."
        ) from exc
    except OSError as exc:
        raise GitCommandError(
            f"Impossibile eseguire Git nel repository: {repository}"
        ) from exc

    if result.returncode != 0:
        error_message = result.stderr.strip() or "Errore Git non specificato"

        raise GitCommandError(
            f"Comando Git fallito nel repository {repository}: "
            f"{error_message}"
        )

    return result.stdout.strip()


def sanitize_remote_url(url: str) -> str:
    """
    Rimuove credenziali, query string e fragment da un remote Git.

    Esempi:
        https://user:token@github.com/owner/repo.git
        -> https://github.com/owner/repo.git

        git@github.com:owner/repo.git
        -> github.com:owner/repo.git
    """
    if not isinstance(url, str):
        raise TypeError("Remote URL must be a string")

    url = url.strip()

    if not url:
        return ""

    # Un percorso locale Windows non deve essere interpretato come
    # una remote SCP-style.
    if _WINDOWS_PATH_PATTERN.match(url):
        return url

    # URL standard: https://, ssh://, git://, file://...
    if "://" in url:
        try:
            parsed = urlsplit(url)

            hostname = parsed.hostname or ""

            # Mantiene correttamente gli indirizzi IPv6.
            if ":" in hostname and not hostname.startswith("["):
                hostname = f"[{hostname}]"

            try:
                port = parsed.port
            except ValueError:
                port = None

            netloc = hostname

            if port is not None:
                netloc = f"{netloc}:{port}"

            return urlunsplit(
                (
                    parsed.scheme,
                    netloc,
                    parsed.path,
                    "",  # Elimina query string.
                    "",  # Elimina fragment.
                )
            )
        except ValueError:
            # Fallback conservativo: elimina eventuale userinfo.
            prefix, separator, remainder = url.partition("://")

            if separator:
                remainder = remainder.rsplit("@", maxsplit=1)[-1]
                remainder = remainder.split("?", maxsplit=1)[0]
                remainder = remainder.split("#", maxsplit=1)[0]

                return f"{prefix}://{remainder}"

            return "<invalid-remote-url>"

    # Remote SCP-style:
    # git@github.com:owner/repository.git
    match = _SCP_REMOTE_PATTERN.match(url)

    if match:
        host = match.group("host")
        path = match.group("path")

        # Rimuove l'utente, ad esempio "git@".
        return f"{host}:{path}"

    # Eventuale URL anomalo senza schema ma contenente userinfo.
    if "@" in url:
        return url.rsplit("@", maxsplit=1)[-1]

    return url.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]


def _normalize_remote_path(path: str) -> str:
    """
    Normalizza la parte path di un remote per il confronto.
    """
    normalized = unquote(path)
    normalized = normalized.replace("\\", "/")
    normalized = normalized.strip("/")

    while "//" in normalized:
        normalized = normalized.replace("//", "/")

    if normalized.lower().endswith(".git"):
        normalized = normalized[:-4]

    return normalized.rstrip("/")


def _normalize_remote_url(
    url: str,
) -> tuple[str, str, int | None, str]:
    """
    Converte remote HTTPS, SSH e SCP-style in una rappresentazione
    confrontabile.

    Gli URL seguenti vengono considerati equivalenti:

        https://github.com/owner/repository.git
        ssh://git@github.com/owner/repository.git
        git@github.com:owner/repository.git
    """
    if not isinstance(url, str):
        raise TypeError("Remote URL must be a string")

    raw_url = url.strip()

    if not raw_url:
        raise RepositoryValidationError("Remote URL cannot be empty")

    if _WINDOWS_PATH_PATTERN.match(raw_url):
        local_path = Path(raw_url).expanduser().resolve(strict=False)

        return (
            "local",
            "",
            None,
            os.path.normcase(str(local_path)),
        )

    if "://" in raw_url:
        try:
            parsed = urlsplit(raw_url)
        except ValueError as exc:
            raise RepositoryValidationError(
                "Invalid remote URL"
            ) from exc

        scheme = parsed.scheme.lower()

        if scheme == "file":
            local_path = Path(unquote(parsed.path)).expanduser().resolve(
                strict=False
            )

            return (
                "local",
                "",
                None,
                os.path.normcase(str(local_path)),
            )

        hostname = (parsed.hostname or "").lower()

        if not hostname:
            raise RepositoryValidationError(
                "Remote URL does not contain a hostname"
            )

        try:
            port = parsed.port
        except ValueError as exc:
            raise RepositoryValidationError(
                "Remote URL contains an invalid port"
            ) from exc

        # Le porte standard non devono rendere differenti due remote
        # equivalenti.
        default_ports = {
            "http": 80,
            "https": 443,
            "ssh": 22,
            "git": 9418,
        }

        if port == default_ports.get(scheme):
            port = None

        return (
            "network",
            hostname,
            port,
            _normalize_remote_path(parsed.path),
        )

    scp_match = _SCP_REMOTE_PATTERN.match(raw_url)

    if scp_match:
        hostname = scp_match.group("host").strip("[]").lower()
        remote_path = _normalize_remote_path(scp_match.group("path"))

        return (
            "network",
            hostname,
            None,
            remote_path,
        )

    # Se non è una remote di rete, viene trattata come repository locale.
    local_path = Path(raw_url).expanduser().resolve(strict=False)

    return (
        "local",
        "",
        None,
        os.path.normcase(str(local_path)),
    )


def validate_existing_checkout(
    path: str | Path,
    expected_remote: str,
) -> Path:
    """
    Verifica che path sia un checkout Git valido.

    Controlla:

    1. esistenza della directory;
    2. presenza di `.git`, directory o file;
    3. repository Git valido;
    4. path corrispondente alla root del worktree;
    5. remote `origin` corrispondente a expected_remote;
    6. worktree pulito, inclusi i file non tracciati.

    Restituisce il path assoluto e risolto del checkout.
    """
    try:
        checkout = Path(path).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise RepositoryValidationError(
            f"Checkout does not exist: {path}"
        ) from exc
    except OSError as exc:
        raise RepositoryValidationError(
            f"Cannot resolve checkout path: {path}"
        ) from exc

    if not checkout.is_dir():
        raise RepositoryValidationError(
            f"Checkout is not a directory: {checkout}"
        )

    git_entry = checkout / ".git"

    # Nei normali repository è una directory.
    # Nei Git worktree e in alcuni submodule può essere un file.
    if not git_entry.exists():
        raise RepositoryValidationError(
            f"Missing .git entry in checkout: {checkout}"
        )

    try:
        inside_worktree = _run_git(
            checkout,
            "rev-parse",
            "--is-inside-work-tree",
        )
    except GitCommandError as exc:
        raise RepositoryValidationError(
            f"Invalid Git checkout: {checkout}"
        ) from exc

    if inside_worktree.lower() != "true":
        raise RepositoryValidationError(
            f"Path is not inside a Git worktree: {checkout}"
        )

    try:
        git_root_output = _run_git(
            checkout,
            "rev-parse",
            "--show-toplevel",
        )
        git_root = Path(git_root_output).resolve(strict=True)
    except (GitCommandError, OSError) as exc:
        raise RepositoryValidationError(
            f"Cannot determine Git worktree root: {checkout}"
        ) from exc

    if git_root != checkout:
        raise RepositoryValidationError(
            f"Path is not the repository root. "
            f"Expected {git_root}, received {checkout}"
        )

    try:
        actual_remote = _run_git(
            checkout,
            "remote",
            "get-url",
            "origin",
        )
    except GitCommandError as exc:
        raise RepositoryValidationError(
            f"Repository does not have a valid origin remote: {checkout}"
        ) from exc

    try:
        normalized_actual = _normalize_remote_url(actual_remote)
        normalized_expected = _normalize_remote_url(expected_remote)
    except (TypeError, RepositoryValidationError) as exc:
        raise RepositoryValidationError(
            "Cannot normalize repository remote"
        ) from exc

    if normalized_actual != normalized_expected:
        safe_actual = sanitize_remote_url(actual_remote)
        safe_expected = sanitize_remote_url(expected_remote)

        raise RepositoryValidationError(
            "Repository remote does not match the expected remote. "
            f"Actual: {safe_actual!r}; expected: {safe_expected!r}"
        )

    try:
        status_output = _run_git(
            checkout,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
    except GitCommandError as exc:
        raise RepositoryValidationError(
            f"Cannot inspect repository status: {checkout}"
        ) from exc

    if status_output:
        changed_entries = len(status_output.splitlines())

        raise RepositoryValidationError(
            f"Repository worktree is not clean: "
            f"{changed_entries} changed or untracked entries"
        )

    return checkout


def _is_windows_reparse_point(path: Path) -> bool:
    """
    Controlla se il path è un Windows reparse point.

    Include junction e altri reparse point. Su Linux e macOS restituisce
    sempre False.
    """
    try:
        path_stat = path.lstat()
    except OSError:
        return False

    file_attributes = getattr(
        path_stat,
        "st_file_attributes",
        0,
    )
    reparse_flag = getattr(
        stat,
        "FILE_ATTRIBUTE_REPARSE_POINT",
        0,
    )

    return bool(
        reparse_flag
        and file_attributes & reparse_flag
    )


def _is_junction(path: Path) -> bool:
    """
    Controlla se il path è una junction Windows.

    Path.is_junction è disponibile nelle versioni recenti di Python.
    Nelle versioni precedenti viene usato il controllo sui reparse point.
    """
    is_junction_method = getattr(path, "is_junction", None)

    if callable(is_junction_method):
        try:
            return bool(is_junction_method())
        except OSError:
            return False

    return _is_windows_reparse_point(path)


def is_safe_repository_entry(
    root: str | Path,
    entry: str | Path,
) -> bool:
    """
    Verifica che entry rimanga all'interno del repository root.

    La funzione:

    - normalizza `.` e `..`;
    - rifiuta path assoluti esterni;
    - segue symlink e junction solamente se puntano dentro root;
    - controlla ogni componente del path;
    - rifiuta symlink rotti;
    - rifiuta entry inesistenti;
    - gestisce junction e reparse point Windows.

    Restituisce True se l'entry è sicura, altrimenti False.
    """
    try:
        repository_root = (
            Path(root)
            .expanduser()
            .resolve(strict=True)
        )
    except (OSError, RuntimeError):
        return False

    if not repository_root.is_dir():
        return False

    try:
        candidate_input = Path(entry).expanduser()

        if candidate_input.is_absolute():
            candidate_lexical = candidate_input
        else:
            candidate_lexical = repository_root / candidate_input

        # abspath normalizza segmenti "." e ".." senza seguire symlink.
        candidate_absolute = Path(
            os.path.abspath(candidate_lexical)
        )

        # Controllo lessicale: blocca immediatamente ../outside.
        if not candidate_absolute.is_relative_to(repository_root):
            return False

        relative_path = candidate_absolute.relative_to(repository_root)

    except (OSError, ValueError, RuntimeError):
        return False

    current = repository_root

    # Controlla ogni componente, non soltanto l'ultimo elemento.
    for component in relative_path.parts:
        current = current / component

        try:
            current_stat = current.lstat()
        except (FileNotFoundError, OSError):
            return False

        is_symbolic_link = stat.S_ISLNK(current_stat.st_mode)
        is_junction = _is_junction(current)

        if is_symbolic_link or is_junction:
            try:
                resolved_target = current.resolve(strict=True)
            except (FileNotFoundError, OSError, RuntimeError):
                # Symlink rotto oppure ciclo di symlink.
                return False

            if not resolved_target.is_relative_to(repository_root):
                return False

    try:
        resolved_candidate = candidate_absolute.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return False

    if not resolved_candidate.is_relative_to(repository_root):
        return False

    return True



_REPOSITORY_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def validate_repository_full_name(value: str) -> tuple[str, str]:
    """Validate and split a GitHub ``owner/repository`` identifier."""
    if not isinstance(value, str):
        raise TypeError("repository_full_name deve essere una stringa")
    if value != value.strip() or "\\" in value or "://" in value:
        raise RepositoryValidationError(
            "repository_full_name deve usare il formato owner/repository"
        )

    parts = value.split("/")
    if len(parts) != 2:
        raise RepositoryValidationError(
            "repository_full_name deve usare il formato owner/repository"
        )

    owner, repository = parts
    if any(
        component in {"", ".", ".."}
        or not _REPOSITORY_COMPONENT_PATTERN.fullmatch(component)
        for component in (owner, repository)
    ):
        raise RepositoryValidationError(
            "repository_full_name contiene componenti non validi"
        )

    return owner, repository

def build_public_clone_url(owner: str, repo: str) -> str:
    """Build a credential-free HTTPS clone URL from validated components."""
    return f"https://github.com/{owner}/{repo}.git"

def build_repository_workspace(
    workspace_root: str | Path,
    owner: str,
    repo: str,
    issue_number: int,
) -> Path:
    """Build the deterministic destination for one issue checkout."""
    return Path(workspace_root) / owner / repo / f"issue-{issue_number}"

def resolve_inside_workspace(workspace_root: str | Path, candidate_path: str | Path) -> Path: 
    absolute_path = Path(workspace_root).expanduser().resolve()
    candidate = Path(candidate_path).expanduser()
    
    if not candidate.is_absolute():
        candidate = absolute_path / candidate

    candidate = candidate.resolve()

    if not candidate.is_relative_to(absolute_path):
        raise ValueError(
            f"Path outside workspace: {candidate}"
        )

    return candidate

def validate_issue_number(issue_number: object) -> int:
    if (
        not isinstance(issue_number, int)
        or isinstance(issue_number, bool)
        or issue_number <= 0
    ):
        raise RepositoryValidationError(
            "issue_number deve essere un intero positivo"
        )
    return issue_number


def validate_branch(default_branch: object) -> str:
    if not isinstance(default_branch, str):
        raise TypeError("default_branch deve essere una stringa")

    if (
        not default_branch
        or default_branch != default_branch.strip()
        or default_branch in {".", ".."}
        or default_branch.startswith("-")
        or default_branch.endswith((".", "/", ".lock"))
        or any(part in default_branch for part in _EXCLUDED_CHARS)
        or any(ord(char) < 32 or ord(char) == 127 for char in default_branch)
    ):
        raise RepositoryValidationError("default_branch non è valido")

    return default_branch
