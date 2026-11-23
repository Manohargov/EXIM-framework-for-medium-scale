"""Tools exposed to the deployment Deep Agent.

Safe tools (status, tests, health, read) need no confirmation. Mutating tools
require ``confirmation="DEPLOY-MSM"`` — the operator must pass that phrase in
the request, which is a deliberate human gate before any external action.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from langchain_core.tools import tool

from .config import REPO_ROOT

CONFIRMATION = "DEPLOY-MSM"
_ALLOWED_FILES = {
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    ".env.example",
    "deepagents.toml",
    "AGENTS.md",
}
_MAX_FILE_BYTES = 50_000


def _run(args: list[str], *, timeout: int = 180, cwd: Path = REPO_ROOT) -> str:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": f"timeout after {timeout}s", "args": args})
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc), "args": args})
    return json.dumps({
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "stdout": proc.stdout[-8000:],
        "stderr": proc.stderr[-4000:],
    })


def _safe_path(relative: str) -> Path | None:
    target = (REPO_ROOT / relative).resolve()
    if not target.is_relative_to(REPO_ROOT.resolve()):
        return None
    return target


@tool
def repo_status() -> str:
    """Show the current branch, short git status and last commit (read-only)."""
    return _run(["git", "status", "--short", "--branch"])


@tool
def list_files(path: str = "") -> str:
    """List files in the repo (read-only). Path must stay inside the repo."""
    target = _safe_path(path)
    if target is None or not target.exists():
        return json.dumps({"ok": False, "error": "path outside repo or not found"})
    entries = sorted(p.relative_to(REPO_ROOT).as_posix() for p in target.rglob("*")
                     if p.is_file() and ".venv" not in p.parts and "__pycache__" not in p.parts)
    return json.dumps({"ok": True, "files": entries[:200], "total": len(entries)})


@tool
def read_file(path: str) -> str:
    """Read a text file inside the repo (read-only)."""
    target = _safe_path(path)
    if target is None or not target.is_file():
        return json.dumps({"ok": False, "error": "path outside repo or not a file"})
    try:
        return json.dumps({"ok": True, "path": path, "content": target.read_text()[:20000]})
    except (OSError, UnicodeDecodeError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool
def run_tests() -> str:
    """Run the full pytest suite with the bundled virtualenv (safe, no network)."""
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else "python3"
    return _run([python, "-m", "pytest", "-q"], timeout=600)


@tool
def health_check() -> str:
    """Check the running console at http://127.0.0.1:8501 (read-only)."""
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=5) as response:
            body = response.read().decode("utf-8", "replace").strip()
        return json.dumps({"ok": response.status == 200, "status": response.status, "body": body})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool
def write_deployment_file(name: str, content: str, confirmation: str) -> str:
    """Write an allowed deployment file. Requires confirmation='DEPLOY-MSM'."""
    if confirmation != CONFIRMATION:
        return json.dumps({"ok": False, "error": "confirmation required"})
    if name not in _ALLOWED_FILES:
        return json.dumps({"ok": False, "error": f"file not allowed: {name}"})
    if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
        return json.dumps({"ok": False, "error": "content too large"})
    target = REPO_ROOT / name
    target.write_text(content)
    return json.dumps({"ok": True, "path": name, "bytes": target.stat().st_size})


@tool
def git_commit(message: str, confirmation: str) -> str:
    """Stage all changes and commit with a message. Requires confirmation."""
    if confirmation != CONFIRMATION:
        return json.dumps({"ok": False, "error": "confirmation required"})
    if not message.strip():
        return json.dumps({"ok": False, "error": "commit message required"})
    staged = _run(["git", "add", "-A"])
    if not json.loads(staged)["ok"]:
        return staged
    return _run(["git", "commit", "-m", message.strip()])


@tool
def docker_build(confirmation: str) -> str:
    """Build the local Docker image msm-console:local. Requires confirmation."""
    if confirmation != CONFIRMATION:
        return json.dumps({"ok": False, "error": "confirmation required"})
    if not shutil.which("docker"):
        return json.dumps({"ok": False, "error": "docker is not installed"})
    return _run(["docker", "build", "-t", "msm-console:local", "."], timeout=900)


@tool
def docker_up(confirmation: str) -> str:
    """Start the console with docker compose up -d. Requires confirmation."""
    if confirmation != CONFIRMATION:
        return json.dumps({"ok": False, "error": "confirmation required"})
    if not shutil.which("docker"):
        return json.dumps({"ok": False, "error": "docker is not installed"})
    return _run(["docker", "compose", "up", "-d"], timeout=600)


@tool
def docker_down(confirmation: str) -> str:
    """Stop the console with docker compose down. Requires confirmation."""
    if confirmation != CONFIRMATION:
        return json.dumps({"ok": False, "error": "confirmation required"})
    if not shutil.which("docker"):
        return json.dumps({"ok": False, "error": "docker is not installed"})
    return _run(["docker", "compose", "down"], timeout=300)


@tool
def show_config() -> str:
    """Show non-secret deployment settings (model endpoint, repo path, Docker)."""
    return json.dumps({
        "repo": str(REPO_ROOT),
        "base_url": os.environ.get("ECOMOPS_DEEPAGENTS_BASE_URL", "http://127.0.0.1:38285/v1"),
        "model": os.environ.get("ECOMOPS_DEEPAGENTS_MODEL", "MiMo-V2.6-Distill-Qwen-9B-Q8_0"),
        "docker": bool(shutil.which("docker")),
        "api_key_set": bool(os.environ.get("ECOMOPS_DEEPAGENTS_API_KEY"))
        or bool(list(Path.home().glob(".unsloth/studio/auth/llama_api_key_*"))),
    })


TOOLS = [
    repo_status,
    list_files,
    read_file,
    run_tests,
    health_check,
    show_config,
    write_deployment_file,
    git_commit,
    docker_build,
    docker_up,
    docker_down,
]

__all__ = ["CONFIRMATION", "TOOLS", *[t.__name__ for t in TOOLS]]
