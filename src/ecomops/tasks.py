"""Task dashboard read model.

The bus is the only store: a task is one ``TaskExtracted`` event, and its current
state is the last ``TaskStatusChanged`` for that id. Folding is pure, so the UI
just asks ``my_tasks(bus, email)`` and never mutates history.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .bus import EventBus
from .events import TaskExtracted, TaskStatusChanged

TASK_STATUSES: tuple[str, ...] = ("open", "in_progress", "done")


class TaskError(RuntimeError):
    """An invalid status update or an unknown task id."""


@dataclass(frozen=True)
class TaskView:
    task_id: str
    title: str
    description: str
    assignee_email: str
    priority: str
    due_date: str | None
    status: str
    source: str
    source_event_id: str | None
    sender: str
    created_at: datetime
    updated_at: datetime


def my_tasks(bus: EventBus, email: str, *, all_users: bool = False) -> list[TaskView]:
    """Current tasks for one operator, newest first.

    ``all_users`` is for managers: the same fold without the assignee filter.
    """
    wanted = (email or "").strip().lower()
    views: dict[str, TaskView] = {}
    for event in bus.of_type("TaskExtracted"):
        assert isinstance(event, TaskExtracted)
        if not all_users and event.assignee_email.strip().lower() != wanted:
            continue
        views[event.task_id] = TaskView(
            task_id=event.task_id,
            title=event.title,
            description=event.description,
            assignee_email=event.assignee_email,
            priority=event.priority,
            due_date=event.due_date,
            status=event.status,
            source=event.origin,
            source_event_id=event.source_event_id,
            sender=event.sender,
            created_at=event.ts,
            updated_at=event.ts,
        )
    for event in bus.of_type("TaskStatusChanged"):
        assert isinstance(event, TaskStatusChanged)
        current = views.get(event.task_id)
        if current is None:
            continue
        views[event.task_id] = replace(
            current, status=event.status, updated_at=event.ts
        )
    return sorted(views.values(), key=lambda view: view.created_at, reverse=True)


def task_counts(tasks: list[TaskView]) -> dict[str, int]:
    """Small summary for the dashboard's filter chips."""
    return {
        "all": len(tasks),
        "open": sum(1 for task in tasks if task.status == "open"),
        "in_progress": sum(1 for task in tasks if task.status == "in_progress"),
        "done": sum(1 for task in tasks if task.status == "done"),
    }


def change_status(
    bus: EventBus, task_id: str, status: str, *, changed_by: str
) -> TaskView:
    """Publish a status transition and return the resulting view.

    The caller drains the bus. Raises ``TaskError`` for an unknown task id or
    an unsupported status — the dashboard never accepts arbitrary strings.
    """
    if status not in TASK_STATUSES:
        raise TaskError(f"unknown task status: {status!r}")
    extracted = [
        event for event in bus.of_type("TaskExtracted")
        if isinstance(event, TaskExtracted) and event.task_id == task_id
    ]
    if not extracted:
        raise TaskError(f"unknown task id: {task_id!r}")
    previous = my_tasks(bus, extracted[0].assignee_email, all_users=True)
    current = next((view for view in previous if view.task_id == task_id), None)
    if current is None:
        raise TaskError(f"unknown task id: {task_id!r}")
    bus.publish(
        TaskStatusChanged(
            task_id=task_id,
            status=status,
            previous_status=current.status,
            changed_by=changed_by,
            source="console",
        )
    )
    return replace(current, status=status)


__all__ = [
    "TASK_STATUSES",
    "TaskError",
    "TaskView",
    "change_status",
    "my_tasks",
    "task_counts",
]
