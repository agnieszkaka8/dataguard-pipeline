import json
import os
import time
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions
from supabase_auth.errors import AuthApiError, AuthRetryableError
from supabase_auth.types import Session as SDKSession

from dataguard.models import Session

console = Console(no_color=bool(os.environ.get("NO_COLOR")))

_SESSION_PATH = Path(".dataguard_session")

AuthErrorReason = Literal["invalid_credentials", "network", "session_revoked"]


class AuthError(Exception):
    """Raised when login or session refresh cannot produce a usable Session."""

    def __init__(self, reason: AuthErrorReason) -> None:
        self.reason: AuthErrorReason = reason
        super().__init__(reason)


def ensure_session() -> Session:
    """Return a valid Session, refreshing or prompting for login as needed.

    Priority: valid cached session > silent refresh > interactive login.
    """
    cached = load_cached_session()
    now = int(time.time())

    if cached is not None and cached.expires_at > now:
        return cached

    if cached is not None:
        try:
            refreshed = _refresh(cached.refresh_token)
        except AuthError:
            pass  # refresh token no longer valid — fall through to interactive login
        else:
            save_session(refreshed)
            return refreshed

    session = _login()
    save_session(session)
    return session


def load_cached_session() -> Session | None:
    """Read the cached session. Missing or corrupted cache -> None (forces login)."""
    if not _SESSION_PATH.exists():
        return None
    try:
        data = json.loads(_SESSION_PATH.read_text())
        return Session(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
        )
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def save_session(session: Session) -> None:
    """Persist session as JSON, restricted to owner read/write only."""
    _SESSION_PATH.write_text(
        json.dumps(
            {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expires_at": session.expires_at,
            }
        )
    )
    os.chmod(_SESSION_PATH, 0o600)


def clear_session() -> None:
    """Delete the cached session, if present."""
    _SESSION_PATH.unlink(missing_ok=True)


def _client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    # auto_refresh_token=False: a dataguard run is short-lived, so the SDK's
    # background daemon-thread refresher would outlive the run for no benefit
    # while adding an unsynchronized race against our own session handling.
    return create_client(url, key, options=SyncClientOptions(auto_refresh_token=False))


def _login() -> Session:
    email = typer.prompt("Supabase email")
    password = typer.prompt("Supabase password", hide_input=True)
    client = _client()
    try:
        response = client.auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except AuthRetryableError as exc:
        raise AuthError("network") from exc
    except AuthApiError as exc:
        raise AuthError("invalid_credentials") from exc
    if response.session is None:
        raise AuthError("invalid_credentials")
    return _to_session(response.session)


def _refresh(refresh_token: str) -> Session:
    client = _client()
    try:
        response = client.auth.refresh_session(refresh_token)
    except AuthRetryableError as exc:
        raise AuthError("network") from exc
    except AuthApiError as exc:
        raise AuthError("session_revoked") from exc
    if response.session is None:
        raise AuthError("session_revoked")
    return _to_session(response.session)


def _to_session(sdk_session: SDKSession) -> Session:
    expires_at = sdk_session.expires_at
    if expires_at is None:
        expires_at = int(time.time()) + sdk_session.expires_in
    return Session(
        access_token=sdk_session.access_token,
        refresh_token=sdk_session.refresh_token,
        expires_at=expires_at,
    )
