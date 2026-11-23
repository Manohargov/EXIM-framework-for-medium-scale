"""Task Extraction Agent.

Reads the two inbound sources — an operator's IMAP mailbox (``EmailReceived``)
and a customer form (``FormReceived``) — and turns their text into actionable
``TaskExtracted`` events. One deterministic rule handles both offline; when a
real model is configured, ``FallbackLLM`` uses the same task name and schema.
"""

from __future__ import annotations

from ..events import (
    EmailReceived,
    Event,
    FormReceived,
    TaskExtracted,
    TaskExtractionCompleted,
    TaskList,
    new_id,
)
from .base import Agent

_INSTRUCTIONS = (
    "Extract actionable tasks as JSON with a 'tasks' array. Each task has "
    "title, description, assignee_email, priority (low|normal|high) and due_date. "
    "Only list work the text actually asks for; never invent tasks."
)


class TaskExtractionAgent(Agent):
    name = "task-extraction"

    def attach(self) -> None:
        self.bus.subscribe(self.name, self.handle, "EmailReceived", "FormReceived")
        self.bus.subscribe(self.name, self.handle, "ActionApproved", "ActionRejected")

    async def react(self, event: Event) -> None:
        if isinstance(event, EmailReceived):
            source = "email"
            body = event.body
            context = {"subject": event.subject}
            sender = event.sender
            assignee = event.assignee_email
        elif isinstance(event, FormReceived):
            source = "form"
            body = event.text or "\n".join(
                f"{key}: {value}" for key, value in event.fields.items()
            )
            context = {"form_name": event.form_name}
            sender = event.submitted_by
            assignee = event.assignee_email
        else:  # pragma: no cover - bus only routes the two subscribed topics
            return

        result = await self.ask(
            "extract_tasks",
            _INSTRUCTIONS,
            {
                "body": body,
                "assignee_email": assignee,
                **context,
            },
            schema=TaskList.model_json_schema(),
        )
        tasks = TaskList.model_validate(result.data).tasks

        for task in tasks:
            self.bus.publish(
                TaskExtracted(
                    task_id=f"task-{new_id()}",
                    title=task.title,
                    description=task.description,
                    assignee_email=(task.assignee_email or assignee).strip(),
                    priority=task.priority if task.priority in {"low", "normal", "high"} else "normal",
                    due_date=task.due_date,
                    status="open",
                    origin=source,
                    source_event_id=event.event_id,
                    sender=sender,
                    source=self.name,
                )
            )

        self.bus.publish(
            TaskExtractionCompleted(
                origin=source,
                count=len(tasks),
                assignee_email=assignee,
                source_event_id=event.event_id,
                llm_backend=result.backend,
                source=self.name,
            )
        )

    # Task extraction has no side effects to approve.
    async def act(self, proposal_id: str, action: str, parameters) -> None:  # pragma: no cover
        return None


__all__ = ["TaskExtractionAgent"]
