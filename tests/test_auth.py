import stat
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest
from supabase_auth.errors import AuthApiError, AuthRetryableError

from dataguard import auth
from dataguard.models import Session


def _session_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".dataguard_session"
    monkeypatch.setattr("dataguard.auth._SESSION_PATH", path)
    return path


def _fake_sdk_session(
    access_token: str = "new-access",
    refresh_token: str = "new-refresh",
    expires_in: int = 3600,
    expires_at: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        expires_at=expires_at,
    )


def _prompt_stub(
    email: str = "engineer@example.com", password: str = "secret"
) -> Callable[..., str]:
    def _prompt(text: str, hide_input: bool = False) -> str:
        return password if hide_input else email

    return _prompt


def _failing_prompt(*_args: Any, **_kwargs: Any) -> str:
    raise AssertionError("interactive prompt should not have been called")


# ---------------------------------------------------------------------------
# load_cached_session / save_session / clear_session
# ---------------------------------------------------------------------------


def test_load_cached_session_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session_path(tmp_path, monkeypatch)
    assert auth.load_cached_session() is None


def test_load_cached_session_corrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _session_path(tmp_path, monkeypatch)
    path.write_text("not-json")
    assert auth.load_cached_session() is None


def test_save_session_writes_0600_and_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _session_path(tmp_path, monkeypatch)
    session = Session(access_token="at", refresh_token="rt", expires_at=123)

    auth.save_session(session)

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    assert auth.load_cached_session() == session


def test_clear_session_removes_file_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _session_path(tmp_path, monkeypatch)
    path.write_text("{}")

    auth.clear_session()
    assert not path.exists()
    auth.clear_session()  # no error when already absent


# ---------------------------------------------------------------------------
# ensure_session
# ---------------------------------------------------------------------------


def test_ensure_session_no_cache_prompts_and_logs_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session_path(tmp_path, monkeypatch)
    monkeypatch.setattr("dataguard.auth.typer.prompt", _prompt_stub())

    future = int(time.time()) + 3600
    response = SimpleNamespace(session=_fake_sdk_session(expires_at=future))
    client = MagicMock()
    client.auth.sign_in_with_password.return_value = response
    monkeypatch.setattr("dataguard.auth._client", lambda: client)

    session = auth.ensure_session()

    assert session.access_token == "new-access"
    assert auth.load_cached_session() == session


def test_ensure_session_valid_cached_session_reused_without_prompt_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session_path(tmp_path, monkeypatch)
    future = int(time.time()) + 3600
    cached = Session(
        access_token="cached-access", refresh_token="cached-refresh", expires_at=future
    )
    auth.save_session(cached)

    monkeypatch.setattr("dataguard.auth.typer.prompt", _failing_prompt)
    monkeypatch.setattr("dataguard.auth._client", _failing_prompt)

    assert auth.ensure_session() == cached


def test_ensure_session_expired_access_valid_refresh_silently_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session_path(tmp_path, monkeypatch)
    past = int(time.time()) - 10
    cached = Session(
        access_token="old-access", refresh_token="old-refresh", expires_at=past
    )
    auth.save_session(cached)

    monkeypatch.setattr("dataguard.auth.typer.prompt", _failing_prompt)

    future = int(time.time()) + 3600
    response = SimpleNamespace(
        session=_fake_sdk_session(access_token="refreshed", expires_at=future)
    )
    client = MagicMock()
    client.auth.refresh_session.return_value = response
    monkeypatch.setattr("dataguard.auth._client", lambda: client)

    session = auth.ensure_session()

    assert session.access_token == "refreshed"
    cached_after = auth.load_cached_session()
    assert cached_after is not None
    assert cached_after.access_token == "refreshed"


def test_ensure_session_expired_refresh_fails_falls_back_to_interactive_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session_path(tmp_path, monkeypatch)
    past = int(time.time()) - 10
    cached = Session(
        access_token="old-access", refresh_token="old-refresh", expires_at=past
    )
    auth.save_session(cached)

    monkeypatch.setattr("dataguard.auth.typer.prompt", _prompt_stub())

    future = int(time.time()) + 3600
    login_response = SimpleNamespace(
        session=_fake_sdk_session(access_token="fresh-login", expires_at=future)
    )
    call_count = {"n": 0}

    def _client_factory() -> MagicMock:
        call_count["n"] += 1
        client = MagicMock()
        if call_count["n"] == 1:
            client.auth.refresh_session.side_effect = AuthApiError(
                "session not found", 401, "session_not_found"
            )
        else:
            client.auth.sign_in_with_password.return_value = login_response
        return client

    monkeypatch.setattr("dataguard.auth._client", _client_factory)

    session = auth.ensure_session()

    assert session.access_token == "fresh-login"


# ---------------------------------------------------------------------------
# Login failure reasons
# ---------------------------------------------------------------------------


def test_login_invalid_credentials_raises_auth_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session_path(tmp_path, monkeypatch)
    monkeypatch.setattr("dataguard.auth.typer.prompt", _prompt_stub())

    def _raise(_credentials: dict[str, str]) -> None:
        raise AuthApiError("invalid credentials", 400, "invalid_credentials")

    client = MagicMock()
    client.auth.sign_in_with_password.side_effect = _raise
    monkeypatch.setattr("dataguard.auth._client", lambda: client)

    with pytest.raises(auth.AuthError) as exc_info:
        auth.ensure_session()

    assert exc_info.value.reason == "invalid_credentials"
    assert auth.load_cached_session() is None


def test_login_network_error_raises_auth_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session_path(tmp_path, monkeypatch)
    monkeypatch.setattr("dataguard.auth.typer.prompt", _prompt_stub())

    def _raise(_credentials: dict[str, str]) -> None:
        raise AuthRetryableError("connection failed", 0)

    client = MagicMock()
    client.auth.sign_in_with_password.side_effect = _raise
    monkeypatch.setattr("dataguard.auth._client", lambda: client)

    with pytest.raises(auth.AuthError) as exc_info:
        auth.ensure_session()

    assert exc_info.value.reason == "network"


def test_to_session_computes_expiry_from_expires_in_when_expires_at_missing() -> None:
    sdk_session = _fake_sdk_session(expires_in=100, expires_at=None)
    before = int(time.time())

    session = auth._to_session(sdk_session)

    after = int(time.time())
    assert before + 100 <= session.expires_at <= after + 100
