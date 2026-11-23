"""Deployment automation agent for the MSM console.

Built with the LangChain Deep Agents SDK. The agent uses read-only inspection
by default; destructive actions require a confirmation token in the tool call.
"""

from .agent import create_deployment_agent

__all__ = ["create_deployment_agent"]
