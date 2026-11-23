"""CLI for the deployment Deep Agent.

    .venv/bin/python -m deploy_agent.cli "check repo status and run tests"
    .venv/bin/python -m deploy_agent.cli   # interactive prompt
"""

from __future__ import annotations

import sys

from .agent import create_deployment_agent


def main() -> int:
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        question = input("Deployment request: ").strip()
    if not question:
        print("No request given.")
        return 1
    agent = create_deployment_agent()
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    messages = result.get("messages", [])
    if messages:
        content = getattr(messages[-1], "content", messages[-1])
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
