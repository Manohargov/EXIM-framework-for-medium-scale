"""UI smoke tests: sign-in gate, role-driven tabs, demo/return flows.

Skipped automatically when the optional ``[ui]`` extra is not installed, so the
suite stays green without Streamlit.
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "src" / "ecomops" / "app.py")


@pytest.fixture(autouse=True)
def _fast_demo(monkeypatch):
    # Autologin as the MD and skip the 1,481-row grocery merge for speed.
    monkeypatch.setenv("ECOMOPS_AUTH_AUTOLOGIN", "md")
    monkeypatch.setenv("ECOMOPS_GROCERY", "none")


def _run(role: str | None = "md") -> AppTest:
    if role is not None:
        os.environ["ECOMOPS_AUTH_AUTOLOGIN"] = role
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


def _metrics(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


_NAV_KEYS = {
    "Overview": "overview", "Customer request": "request",
    "Stock clearance": "stock", "Transit": "transit", "Returns": "returns",
    "Approvals": "approvals", "Event trace": "trace", "Inventory": "inventory",
    "Referrals & finance": "finance", "Tasks": "tasks",
}


def _nav_buttons(at: AppTest) -> list:
    return [button for button in at.button if (button.key or "").startswith("icon_")]


def _nav_keys(at: AppTest) -> list[str]:
    return [button.key.removeprefix("icon_") for button in _nav_buttons(at)]


def _nav_button(at: AppTest, label: str):
    key = f"icon_{_NAV_KEYS[label]}"
    return next(button for button in at.button if button.key == key)


def test_app_renders_with_all_nav_items():
    at = _run()
    assert at.title[0].value == "MSM"
    assert len(_nav_keys(at)) == 10
    assert _nav_keys(at)[-1] == "tasks"
    assert at.session_state["view"] == "overview"
    assert _metrics(at)["Events"] == "0"


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("md", 10),
        ("supervisor", 9),
        ("staff", 7),
        ("accountant", 2),
    ],
)
def test_role_drives_visible_nav(role, expected):
    at = _run(role)
    keys = _nav_keys(at)
    assert len(keys) == expected
    if role != "accountant":
        assert keys[-1] == "tasks"
    assert ("finance" in keys) is (role in {"md", "accountant"})
    assert ("trace" in keys) is (role in {"md", "supervisor"})
    assert ("approvals" in keys) is (role in {"md", "supervisor", "accountant"})


def test_login_gate_without_autologin(monkeypatch):
    monkeypatch.delenv("ECOMOPS_AUTH_AUTOLOGIN", raising=False)
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == "MSM"
    assert _nav_keys(at) == []
    assert any("Sign in" in b.label for b in at.button)

    at.text_input[0].set_value("msm")
    next(b for b in at.button if b.label == "Sign in").click().run()
    assert not at.exception, at.exception
    assert len(_nav_keys(at)) == 10  # the default demo account is the MD


def test_sidebar_assistant_answers():
    at = _run("staff")
    at.toggle[0].set_value(True).run()
    at.chat_input[0].set_value("hello").run()
    assert not at.exception, at.exception
    messages = [m.markdown[0].value for m in at.chat_message if m.markdown]
    assert messages and "Staff" in messages[-1]

    at.chat_input[0].set_value("what is our revenue?").run()
    assert not at.exception, at.exception
    messages = [m.markdown[0].value for m in at.chat_message if m.markdown]
    assert "restricted" in messages[-1]


def test_assistant_starts_collapsed_and_can_be_opened():
    at = _run("md")
    assert at.toggle[0].label == "Assistant" and at.toggle[0].value is False
    assert len(at.chat_input) == 0

    at.toggle[0].set_value(True).run()
    assert not at.exception, at.exception
    assert len(at.chat_input) == 1
    assert len(_nav_keys(at)) == 10


def test_sidebar_navigation_switches_the_view():
    at = _run()
    _nav_button(at, "Referrals & finance").click().run()
    assert not at.exception, at.exception
    assert at.session_state["view"] == "finance"
    assert "Referral revenue" in {metric.label for metric in at.metric}

    _nav_button(at, "Tasks").click().run()
    assert not at.exception, at.exception
    assert at.session_state["view"] == "tasks"


def test_finance_values_are_hidden_from_operations_roles():
    staff = _run("staff")
    labels = [m.label for m in staff.metric]
    assert "Gross revenue" not in labels and "Inventory value" not in labels
    _nav_button(staff, "Inventory").click().run()
    inventory = next(df.value for df in staff.dataframe if "SKU" in df.value.columns)
    assert "Value" not in inventory.columns

    accountant = _run("accountant")
    labels = [m.label for m in accountant.metric]
    assert "Gross revenue" in labels and "Inventory value" in labels
    _nav_button(accountant, "Referrals & finance").click().run()
    assert not any("SKU" in df.value.columns for df in accountant.dataframe)

    md = _run("md")
    _nav_button(md, "Inventory").click().run()
    inventory = next(df.value for df in md.dataframe if "SKU" in df.value.columns)
    assert "Value" in inventory.columns


def test_demo_flow_then_grant_all():
    at = _run()

    next(b for b in at.button if "Run demo" in b.label).click().run()
    assert not at.exception, at.exception
    m = _metrics(at)
    assert int(m["Events"]) > 0
    assert int(m["Pending"]) > 0
    assert int(m["Shipments"]) > 0

    _nav_button(at, "Approvals").click().run()
    next(b for b in at.button if "Grant all" in b.label).click().run()
    assert not at.exception, at.exception
    m = _metrics(at)
    assert m["Pending"] == "0"
    assert int(m["Discounts"]) > 0


def test_return_flow_then_grant_refund():
    at = _run()

    next(b for b in at.button if "Run demo" in b.label).click().run()
    _nav_button(at, "Approvals").click().run()
    next(b for b in at.button if "Grant all" in b.label).click().run()
    assert not at.exception, at.exception

    _nav_button(at, "Returns").click().run()
    next(b for b in at.button if "Report damaged item" in b.label).click().run()
    assert not at.exception, at.exception
    assert int(_metrics(at)["Pending"]) > 0

    _nav_button(at, "Approvals").click().run()
    next(b for b in at.button if "Grant all" in b.label).click().run()
    assert not at.exception, at.exception
    assert _metrics(at)["Refunds"] != "$0.00"
    assert int(_metrics(at)["Returns"]) > 0


def test_sidebar_does_not_show_the_policy_block():
    at = _run()
    assert "**Policy**" not in [m.value for m in at.markdown]
