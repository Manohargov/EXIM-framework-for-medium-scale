"""ecomops — event-driven multi-agent system for e-commerce operations.

Four specialised agents, connected by an event bus and an approval gate:

    Customer Requirements Agent -> Stock Clearance Agent -> Transit Management Agent
              |                        Returns & Refunds Agent
              \\________________________ Approval Gate ________________________/

Everything is in-process and deterministic; the LLM (local MLX via mlx-lm, e.g.
Qwen3-4B-4bit / DeepSeek-R1-Distill) has a rule-based fallback so the system
runs and tests with no model installed.
"""

__version__ = "0.4.0"
