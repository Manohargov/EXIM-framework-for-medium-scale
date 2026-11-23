"""Deployment agent tools: safety gates, path scoping and read-only checks."""

from __future__ import annotations

import json

from deploy_agent import tools


def _call(tool, *args, **kwargs):
    return json.loads(tool.func(*args, **kwargs))


def test_mutating_tools_require_confirmation(tmp_path):
    assert not _call(tools.git_commit, "message", "wrong")["ok"]
    assert not _call(tools.write_deployment_file, "Dockerfile", "FROM scratch", "wrong")["ok"]
    assert not _call(tools.docker_build, "wrong")["ok"]
    assert not _call(tools.docker_up, "wrong")["ok"]
    assert not _call(tools.docker_down, "wrong")["ok"]


def test_write_deployment_file_allows_only_allowlisted_names():
    result = _call(tools.write_deployment_file, "evil.sh", "rm -rf /", tools.CONFIRMATION)
    assert not result["ok"]
    assert "not allowed" in result["error"]


def test_list_and_read_stay_inside_repo():
    assert not _call(tools.list_files, "../../etc")["ok"]
    assert not _call(tools.read_file, "../../etc/passwd")["ok"]
    listed = _call(tools.list_files, "deploy_agent")
    assert listed["ok"] and "deploy_agent/tools.py" in listed["files"]


def test_show_config_does_not_leak_the_api_key(monkeypatch):
    monkeypatch.setenv("ECOMOPS_DEEPAGENTS_API_KEY", "super-secret-value")
    result = _call(tools.show_config)
    assert "super-secret-value" not in json.dumps(result)
    assert result["api_key_set"] is True


def test_repo_status_reports_the_local_repo():
    result = _call(tools.repo_status)
    assert result["ok"]
    assert "##" in result["stdout"]  # git branch line


def test_health_check_handles_unreachable_server(monkeypatch):
    import urllib.request

    def down(*_a, **_k):
        raise OSError("server down")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    assert not _call(tools.health_check)["ok"]


def test_tools_are_registered_with_langchain():
    names = {t.name for t in tools.TOOLS}
    assert {"repo_status", "run_tests", "git_commit", "docker_up"} <= names
