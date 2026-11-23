"""Authentication and role-based access control.

Three deployment shapes are supported, all mapping to the same ``User``:

* **Streamlit OIDC (``ECOMOPS_SSO_MODE=oidc``)** — ``st.login()`` runs the
  authorization-code flow against any OIDC provider configured under ``[auth]``
  in ``.streamlit/secrets.toml`` (Microsoft Entra, Google, Okta, Keycloak…).
* **OIDC reverse proxy (``ECOMOPS_SSO_MODE=proxy``)** — oauth2-proxy / Keycloak
  gatekeeper authenticates the browser and forwards
  ``X-Auth-Request-Email`` / ``X-Auth-Request-Groups``. The API trusts those
  headers only when ``X-Ecomops-Proxy-Secret`` matches
  ``ECOMOPS_PROXY_SECRET``.
* **Dev login (``ECOMOPS_SSO_MODE=dev``, the default)** — four demo accounts,
  one per role, so the access matrix can be exercised offline. Never enable
  this on a public deployment.

Roles (least to most privileged): ``staff`` → ``accountant`` / ``supervisor``
→ ``md``. Finance data is isolated: only the accountant and the MD can see it,
and only they can approve money movements (refunds).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# --------------------------------------------------------------------------- #
# Roles and permissions
# --------------------------------------------------------------------------- #


class Role(StrEnum):
    MD = "md"
    SUPERVISOR = "supervisor"
    ACCOUNTANT = "accountant"
    STAFF = "staff"


ROLE_LABELS = {
    Role.MD: "Managing Director",
    Role.SUPERVISOR: "Supervisor",
    Role.ACCOUNTANT: "Accountant",
    Role.STAFF: "Staff",
}

ROLE_RANK = {Role.STAFF: 0, Role.ACCOUNTANT: 1, Role.SUPERVISOR: 2, Role.MD: 3}


class Permission(StrEnum):
    VIEW_OPERATIONS = "view_operations"   # overview, stock, transit, returns, inventory
    ACT_OPERATIONS = "act_operations"     # submit requests, run stock/transit/return actions
    APPROVE_OPERATIONS = "approve_operations"
    VIEW_FINANCE = "view_finance"         # revenue, refunds, referrals
    APPROVE_FINANCE = "approve_finance"   # refunds / money movements
    VIEW_AUDIT = "view_audit"             # event trace
    MANAGE = "manage"                     # users, policy, catalogue settings


# The access matrix from the specification:
#   MD          -> full access
#   Supervisor  -> everything except finance
#   Accountant  -> finance data only (and finance approvals)
#   Staff       -> actions only (no approvals, no finance, no audit)
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.MD: frozenset(Permission),
    Role.SUPERVISOR: frozenset({
        Permission.VIEW_OPERATIONS,
        Permission.ACT_OPERATIONS,
        Permission.APPROVE_OPERATIONS,
        Permission.VIEW_AUDIT,
    }),
    Role.ACCOUNTANT: frozenset({
        Permission.VIEW_FINANCE,
        Permission.APPROVE_FINANCE,
    }),
    Role.STAFF: frozenset({
        Permission.VIEW_OPERATIONS,
        Permission.ACT_OPERATIONS,
    }),
}

# Actions that move money and therefore need a finance approver.
FINANCE_ACTIONS = frozenset({"issue_refund"})


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]


def can(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class User:
    email: str
    name: str
    role: Role
    provider: str = "dev"
    groups: tuple[str, ...] = field(default_factory=tuple)

    def can(self, permission: Permission) -> bool:
        return can(self.role, permission)

    def can_any(self, *permissions: Permission) -> bool:
        return any(self.can(permission) for permission in permissions)

    @property
    def label(self) -> str:
        return ROLE_LABELS[self.role]

    @property
    def initials(self) -> str:
        parts = [p for p in self.name.replace(".", " ").split() if p]
        return "".join(p[0].upper() for p in parts[:2]) or self.email[:1].upper()

    def as_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "name": self.name,
            "role": self.role.value,
            "provider": self.provider,
        }


class AuthError(Exception):
    """Authentication failed (no session, bad token, wrong password)."""


class PermissionDenied(AuthError):
    """The authenticated user lacks the required permission."""


def require(user: User, permission: Permission) -> User:
    if not user.can(permission):
        raise PermissionDenied(
            f"{user.label} ({user.email}) is not allowed to {permission.value.replace('_', ' ')}"
        )
    return user


# --------------------------------------------------------------------------- #
# Demo directory and role mapping
# --------------------------------------------------------------------------- #

DEMO_USERS: dict[str, tuple[str, Role]] = {
    "md@msm.local": ("Morgan Diaz", Role.MD),
    "supervisor@msm.local": ("Sam Rivera", Role.SUPERVISOR),
    "accountant@msm.local": ("Avery Chen", Role.ACCOUNTANT),
    "staff@msm.local": ("Jordan Blake", Role.STAFF),
}

DEFAULT_DEMO_PASSWORD = "msm"


def demo_user(role: Role) -> User:
    for email, (name, candidate) in DEMO_USERS.items():
        if candidate is role:
            return User(email=email, name=name, role=role, provider="dev")
    raise AuthError(f"no demo user for role {role.value}")


def dev_login(email: str, password: str) -> User:
    """Authenticate against the demo directory (``ECOMOPS_SSO_MODE=dev``)."""
    email = (email or "").strip().lower()
    expected = os.environ.get("ECOMOPS_DEMO_PASSWORD", DEFAULT_DEMO_PASSWORD)
    if email not in DEMO_USERS or not hmac.compare_digest(password or "", expected):
        raise AuthError("unknown demo account or wrong password")
    name, role = DEMO_USERS[email]
    return User(email=email, name=name, role=role, provider="dev")


def _json_env(name: str) -> dict[str, str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {str(k).strip().lower(): str(v).strip().lower() for k, v in value.items()} if isinstance(value, dict) else {}


def role_for_identity(email: str, groups: tuple[str, ...] = ()) -> Role:
    """Map an IdP identity to a role.

    Order: explicit e-mail map (``ECOMOPS_ROLE_MAP``) → group map
    (``ECOMOPS_GROUP_MAP``) → demo directory → ``ECOMOPS_DEFAULT_ROLE`` (staff).
    Unknown users therefore get the least privilege, never more.
    """
    email = (email or "").strip().lower()
    email_map = _json_env("ECOMOPS_ROLE_MAP")
    if email in email_map:
        return _role_or_default(email_map[email])
    group_map = _json_env("ECOMOPS_GROUP_MAP")
    for group in groups:
        role = group_map.get(group.strip().lower())
        if role:
            return _role_or_default(role)
    if email in DEMO_USERS:
        return DEMO_USERS[email][1]
    return _role_or_default(os.environ.get("ECOMOPS_DEFAULT_ROLE", Role.STAFF.value))


def _role_or_default(value: str) -> Role:
    try:
        return Role(value)
    except ValueError:
        return Role.STAFF


def user_from_identity(
    email: str,
    name: str | None = None,
    *,
    groups: tuple[str, ...] = (),
    provider: str = "oidc",
) -> User:
    email = (email or "").strip().lower()
    if not email:
        raise AuthError("the identity provider did not return an e-mail address")
    display = name or DEMO_USERS.get(email, (email.split("@")[0], Role.STAFF))[0]
    return User(email=email, name=display, role=role_for_identity(email, groups), provider=provider, groups=groups)


# --------------------------------------------------------------------------- #
# Signed sessions (shared by the UI and the HTTP API)
# --------------------------------------------------------------------------- #


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass
class SessionCodec:
    """HMAC-signed bearer token: ``base64(payload).signature`` (stdlib only)."""

    secret: str
    ttl_seconds: int = 8 * 3600

    @classmethod
    def from_environment(cls) -> SessionCodec:
        return cls(secret=os.environ.get("ECOMOPS_SESSION_SECRET", "msm-dev-secret-change-me"))

    def issue(self, user: User) -> str:
        payload = json.dumps(
            {**user.as_dict(), "exp": int(time.time()) + self.ttl_seconds},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(self.secret.encode("utf-8"), payload, hashlib.sha256).digest()
        return f"{_b64encode(payload)}.{_b64encode(signature)}"

    def verify(self, token: str) -> User:
        try:
            body, signature = token.split(".", 1)
            payload = _b64decode(body)
            expected = hmac.new(self.secret.encode("utf-8"), payload, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _b64decode(signature)):
                raise AuthError("invalid session signature")
            data = json.loads(payload)
        except AuthError:
            raise
        except Exception as exc:  # malformed token
            raise AuthError("malformed session token") from exc
        if int(data.get("exp", 0)) < int(time.time()):
            raise AuthError("session expired — sign in again")
        try:
            role = Role(data["role"])
        except (KeyError, ValueError) as exc:
            raise AuthError("session token has no valid role") from exc
        return User(
            email=str(data.get("email", "")),
            name=str(data.get("name", "")),
            role=role,
            provider=str(data.get("provider", "token")),
        )


# --------------------------------------------------------------------------- #
# Reverse-proxy (OIDC offload) identities
# --------------------------------------------------------------------------- #


def user_from_proxy_headers(headers: dict[str, str]) -> User:
    """Build a user from oauth2-proxy / gatekeeper headers.

    Callers must verify ``X-Ecomops-Proxy-Secret`` first. Accepts both the
    oauth2-proxy (``X-Auth-Request-*``) and generic (``X-Forwarded-*``) names.
    """
    def header(*names: str) -> str:
        for name in names:
            value = headers.get(name.lower()) or headers.get(name)
            if value:
                return value.strip()
        return ""

    email = header("X-Auth-Request-Email", "X-Forwarded-Email", "X-Forwarded-User")
    if "@" not in email:
        raise AuthError("proxy authentication headers are missing a valid e-mail")
    groups_raw = header("X-Auth-Request-Groups", "X-Forwarded-Groups")
    groups = tuple(part.strip() for part in groups_raw.split(",") if part.strip())
    name = header("X-Auth-Request-Preferred-Username", "X-Forwarded-Name") or None
    return user_from_identity(email, name, groups=groups, provider="sso-proxy")


def proxy_secret_ok(headers: dict[str, str]) -> bool:
    expected = os.environ.get("ECOMOPS_PROXY_SECRET")
    supplied = headers.get("x-ecomops-proxy-secret") or headers.get("X-Ecomops-Proxy-Secret")
    return bool(expected) and bool(supplied) and secrets.compare_digest(expected, supplied)


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #


def sso_mode() -> str:
    """``dev`` (default), ``oidc`` (Streamlit native) or ``proxy`` (headers)."""
    mode = os.environ.get("ECOMOPS_SSO_MODE", "dev").strip().lower()
    return mode if mode in {"dev", "oidc", "proxy"} else "dev"


def autologin_role() -> Role | None:
    """``ECOMOPS_AUTH_AUTOLOGIN`` — test/demo shortcut (md|supervisor|accountant|staff)."""
    raw = os.environ.get("ECOMOPS_AUTH_AUTOLOGIN", "").strip().lower()
    if not raw:
        return None
    try:
        return Role(raw)
    except ValueError:
        return None
