"""Deep Agent that automates local deployment of the MSM console."""

from __future__ import annotations

from deepagents import create_deep_agent

from .config import model
from .tools import TOOLS

SYSTEM_PROMPT = """You are the MSM deployment agent. You work in the msm/ repository.

Rules:
1. Inspect first: use repo_status, list_files and read_file before changing anything.
2. Verify: run_tests and health_check are safe and should be used often.
3. Never invent credentials or secrets. Use show_config to see what is configured.
4. Mutating tools (write_deployment_file, git_commit, docker_build, docker_up,
   docker_down) only work when the operator includes the exact confirmation
   phrase DEPLOY-MSM in their request. Ask for it if it is missing.
5. Prefer the smallest change that achieves the request. Do not push to any
   remote — this repository is local only.
6. Report exactly what you changed and the result of each verification step.

Deployment assets already in the repo: Dockerfile, docker-compose.yml,
.dockerignore. The target is a local Docker container serving port 8501."""


def create_deployment_agent():
    """Build the Deep Agent with local GGUF model and deployment tools."""
    return create_deep_agent(
        model=model(),
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )


__all__ = ["SYSTEM_PROMPT", "create_deployment_agent"]
