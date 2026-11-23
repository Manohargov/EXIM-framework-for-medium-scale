"""Task extraction: deterministic rules, event flow, user scoping, dashboard UI."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ecomops import rules
from ecomops.events import EmailReceived, FormReceived, TaskExtracted, TaskStatusChanged
from ecomops.system import build_system
from ecomops.tasks import TaskError, my_tasks, task_counts

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "src" / "ecomops" / "app.py")


def test_extract_tasks_from_checkboxes_and_requests():
    result = rules.extract_tasks({
        "body": (
            "Hi team,\n"
            "- [ ] Review the PO by 2026-11-25\n"
            "Could you send the revised quote urgently?\n"
            "2. Follow up with the carrier\n"
            "Please check the refund status — no rush\n"
        ),
        "assignee_email": "md@msm.local",
    })
    tasks = result["tasks"]
    assert len(tasks) == 4
    assert tasks[0]["title"] == "Review the PO by 2026-11-25"
    assert tasks[0]["due_date"] == "2026-11-25"
    assert tasks[1]["priority"] == "high"
    assert tasks[2]["title"] == "Follow up with the carrier"
    assert tasks[3]["priority"] == "low"
    assert all(task["assignee_email"] == "md@msm.local" for task in tasks)


def test_extract_tasks_from_form_fields_and_deduplication():
    result = rules.extract_tasks({
        "body": "Please review the order\nPlease review the order\n",
        "form_fields": {"task": "Call the supplier", "customer": "Northwind"},
        "assignee_email": "staff@msm.local",
    })
    titles = [task["title"] for task in result["tasks"]]
    assert titles.count("review the order") == 1
    assert "task: Call the supplier" in titles
    assert "customer: Northwind" not in titles


def test_extract_tasks_without_actionable_lines_yields_none():
    assert rules.extract_tasks({"body": "Thanks for the update, looks good."})["tasks"] == []


def test_rule_is_registered_on_the_rule_llm():
    llm = rules.build_rule_llm()
    assert "extract_tasks" in llm.rules


def test_email_and_form_become_scoped_tasks():
    system = build_system()
    asyncio.run(system.receive_email(
        sender="boss@msm.local", subject="Actions",
        body="Please review the stock report\n- [ ] Chase the carrier urgently",
        assignee_email="md@msm.local",
    ))
    asyncio.run(system.receive_form(
        text="Please update the shipment note",
        submitted_by="customer@example.com",
        assignee_email="staff@msm.local",
    ))

    md = my_tasks(system.bus, "md@msm.local")
    staff = my_tasks(system.bus, "staff@msm.local")
    assert [task.title for task in md] == ["Chase the carrier urgently", "review the stock report"]
    assert [task.title for task in staff] == ["update the shipment note"]
    assert all(task.source == "email" for task in md)
    assert staff[0].source == "form"
    assert staff[0].sender == "customer@example.com"
    assert len(system.bus.of_type("TaskExtractionCompleted")) == 2


def test_status_transitions_fold_into_the_current_view():
    system = build_system()
    asyncio.run(system.receive_form(text="Please confirm the refund", assignee_email="md@msm.local"))
    task = my_tasks(system.bus, "md@msm.local")[0]
    assert task.status == "open"

    system.update_task_status(task.task_id, "in_progress", changed_by="md@msm.local")
    current = my_tasks(system.bus, "md@msm.local")[0]
    assert current.status == "in_progress"
    assert current.updated_at > current.created_at

    system.update_task_status(task.task_id, "done", changed_by="md@msm.local")
    final = my_tasks(system.bus, "md@msm.local")[0]
    assert final.status == "done"
    assert task_counts([final]) == {"all": 1, "open": 0, "in_progress": 0, "done": 1}
    assert len(system.bus.of_type(TaskStatusChanged.topic)) == 2


def test_invalid_status_and_unknown_id_are_rejected():
    system = build_system()
    with pytest.raises(TaskError):
        system.update_task_status("task-nope", "done", changed_by="md@msm.local")
    asyncio.run(system.receive_form(text="Please check stock", assignee_email="md@msm.local"))
    task = my_tasks(system.bus, "md@msm.local")[0]
    with pytest.raises(TaskError):
        system.update_task_status(task.task_id, "archived", changed_by="md@msm.local")


def test_task_events_are_registered_for_the_journal():
    from ecomops.events import event_classes

    registered = event_classes()
    for topic in (EmailReceived.topic, FormReceived.topic, TaskExtracted.topic,
                  TaskStatusChanged.topic, "TaskExtractionCompleted"):
        assert topic in registered


@pytest.fixture()
def _app_env(monkeypatch):
    monkeypatch.setenv("ECOMOPS_AUTH_AUTOLOGIN", "md")
    monkeypatch.setenv("ECOMOPS_GROCERY", "none")


def test_tasks_tab_form_creates_dashboard_items(_app_env):
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    next(button for button in at.button if button.key == "icon_tasks").click().run()
    assert not at.exception, at.exception

    at.text_area(key="task_form_text").set_value(
        "Please review the delivery by 2026-11-25\n- [ ] Confirm the refund urgently"
    )
    next(b for b in at.get("form_submit_button") if b.label == "Extract tasks").click().run()
    assert not at.exception, at.exception

    system = at.session_state.system
    tasks = my_tasks(system.bus, "md@msm.local")
    assert {task.title for task in tasks} == {
        "review the delivery by 2026-11-25", "Confirm the refund urgently"
    }
    rendered = " ".join(block.value for block in at.markdown)
    assert "review the delivery by 2026-11-25" in rendered
