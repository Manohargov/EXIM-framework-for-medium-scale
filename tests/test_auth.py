"""Authentication, role matrix and session tokens."""

import json

import pytest

from ecomops.auth import (
    FINANCE_ACTIONS,
    ROLE_PERMISSIONS,
    AuthError,
    Permission,
    Role,
    SessionCodec,
    User,
    autologin_role,
    can,
    demo_user,
    dev_login,
    permissions_for,
    proxy_secret_ok,
    role_for_identity,
    sso_mode,
    user_from_identity,
    user_from_proxy_headers,
)


def test_md_has_every_permission():
    assert permissions_for(Role.MD) == frozenset(Permission)
    assert all(can(Role.MD, permission) for permission in Permission)


def test_supervisor_has_operations_and_audit_but_no_finance():
    permissions = permissions_for(Role.SUPERVISOR)
    assert {Permission.VIEW_OPERATIONS, Permission.ACT_OPERATIONS,
            Permission.APPROVE_OPERATIONS, Permission.VIEW_AUDIT} <= permissions
    assert not permissions & {Permission.VIEW_FINANCE, Permission.APPROVE_FINANCE, Permission.MANAGE}


def test_accountant_is_finance_only():
    assert permissions_for(Role.ACCOUNTANT) == {
        Permission.VIEW_FINANCE,
        Permission.APPROVE_FINANCE,
    }


def test_staff_can_act_but_not_approve_audit_or_finance():
    assert permissions_for(Role.STAFF) == {
        Permission.VIEW_OPERATIONS,
        Permission.ACT_OPERATIONS,
    }
    assert not can(Role.STAFF, Permission.APPROVE_OPERATIONS)
    assert not can(Role.STAFF, Permission.VIEW_AUDIT)


def test_refunds_are_the_only_finance_action():
    assert {"issue_refund"} == FINANCE_ACTIONS


def test_demo_users_cover_every_role():
    assert {user.role for user in (demo_user(role) for role in Role)} == set(Role)
    assert all(ROLE_PERMISSIONS[user.role] for user in (demo_user(role) for role in Role))


def test_dev_login(monkeypatch):
    user = dev_login("MD@msm.local", "msm")
    assert user.role is Role.MD and user.email == "md@msm.local"

    with pytest.raises(AuthError):
        dev_login("md@msm.local", "wrong")
    with pytest.raises(AuthError):
        dev_login("unknown@msm.local", "msm")

    monkeypatch.setenv("ECOMOPS_DEMO_PASSWORD", "s3cret")
    assert dev_login("staff@msm.local", "s3cret").role is Role.STAFF


def test_session_codec_round_trip_and_rejects_tampering():
    codec = SessionCodec(secret="test-secret")
    user = User(email="a@b.c", name="A B", role=Role.SUPERVISOR, provider="dev")
    token = codec.issue(user)
    restored = codec.verify(token)
    assert (restored.email, restored.role) == (user.email, user.role)

    body, signature = token.split(".")
    with pytest.raises(AuthError):
        codec.verify(body + "." + signature[:-2] + "AA")
    with pytest.raises(AuthError):
        SessionCodec(secret="other").verify(token)
    with pytest.raises(AuthError):
        codec.verify("not-a-token")


def test_expired_session_is_rejected():
    codec = SessionCodec(secret="test-secret", ttl_seconds=-10)
    token = codec.issue(User(email="a@b.c", name="A", role=Role.STAFF))
    with pytest.raises(AuthError, match="expired"):
        codec.verify(token)


def test_identity_mapping_order(monkeypatch):
    monkeypatch.setenv("ECOMOPS_ROLE_MAP", json.dumps({"boss@corp.com": "md"}))
    monkeypatch.setenv("ECOMOPS_GROUP_MAP", json.dumps({"msm-finance": "accountant"}))
    monkeypatch.setenv("ECOMOPS_DEFAULT_ROLE", "staff")

    assert role_for_identity("boss@corp.com") is Role.MD
    assert role_for_identity("someone@corp.com", ("msm-finance",)) is Role.ACCOUNTANT
    assert role_for_identity("supervisor@msm.local") is Role.SUPERVISOR
    assert role_for_identity("stranger@corp.com") is Role.STAFF


def test_unknown_identity_gets_least_privilege(monkeypatch):
    monkeypatch.delenv("ECOMOPS_ROLE_MAP", raising=False)
    monkeypatch.delenv("ECOMOPS_GROUP_MAP", raising=False)
    monkeypatch.setenv("ECOMOPS_DEFAULT_ROLE", "not-a-role")
    assert role_for_identity("stranger@corp.com") is Role.STAFF


def test_user_from_identity_requires_email():
    user = user_from_identity("boss@corp.com", "The Boss", provider="oidc")
    assert user.name == "The Boss" and user.provider == "oidc"
    with pytest.raises(AuthError):
        user_from_identity("")


def test_proxy_headers_and_secret(monkeypatch):
    monkeypatch.setenv("ECOMOPS_PROXY_SECRET", "proxy-secret")
    headers = {
        "x-auth-request-email": "avery@corp.com",
        "x-auth-request-groups": "msm-finance",
        "x-ecomops-proxy-secret": "proxy-secret",
    }
    assert proxy_secret_ok(headers)
    assert not proxy_secret_ok({**headers, "x-ecomops-proxy-secret": "wrong"})

    monkeypatch.setenv("ECOMOPS_ROLE_MAP", json.dumps({"avery@corp.com": "accountant"}))
    user = user_from_proxy_headers(headers)
    assert user.role is Role.ACCOUNTANT and user.provider == "sso-proxy"

    with pytest.raises(AuthError):
        user_from_proxy_headers({"x-auth-request-email": "not-an-email"})


def test_modes_and_autologin(monkeypatch):
    monkeypatch.setenv("ECOMOPS_SSO_MODE", "weird")
    assert sso_mode() == "dev"
    monkeypatch.setenv("ECOMOPS_SSO_MODE", "oidc")
    assert sso_mode() == "oidc"

    monkeypatch.setenv("ECOMOPS_AUTH_AUTOLOGIN", "supervisor")
    assert autologin_role() is Role.SUPERVISOR
    monkeypatch.setenv("ECOMOPS_AUTH_AUTOLOGIN", "")
    assert autologin_role() is None
