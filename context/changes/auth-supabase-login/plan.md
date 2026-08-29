# Supabase Auth Login Implementation Plan

## Overview

Gate every `dataguard` CLI invocation behind a per-engineer Supabase Auth login, replacing
the current "possession of `.env` is the only access control" model. Login is interactive
(email/password), the resulting session is cached locally and silently refreshed across
runs, and a new `dataguard logout` command clears the cache. This implements FR-009/FR-010
and the amended §Access Control in `context/foundation/prd.md`.

## Current State Analysis

- `dataguard/cli.py`: Typer app with a single `sync` command; `@app.callback()` (cli.py:18-20)
  is currently docstring-only — the natural single choke point for a gate applied to every
  command. Every existing failure path exits via `typer.Exit(code=1)` (cli.py:43, 55, 67, 94).
- `env_check()` (`dataguard/env_check.py`) validates `SOURCE_DB`, `TARGET_DB`, `SUPABASE_URL`,
  `SUPABASE_KEY` are present before anything else runs (called at cli.py:38); never prints
  values, only var names — the established no-credential-leakage pattern.
- No local session/auth state exists today. `dataguard/watermark.py` is the only local-state-
  file precedent: a bare ISO-timestamp string at `.watermark` in cwd, read via a module-level
  `Path` constant that tests monkeypatch directly (`test_watermark.py:14`).
- A Supabase client is already constructed with the service-role key in `sync.py`'s
  `_connect_supabase()` (sync.py:151-157) to write the rejection log. The CLI already
  distributes that key to every engineer via `.env`, so reusing it to back the auth client
  introduces no new trust boundary.
- No `dataguard login`/`logout` commands exist yet.
- `supabase>=2.25.1` is already a dependency (pyproject.toml); no new package needed. Its
  default client starts a background daemon thread that auto-refreshes sessions on a timer,
  mutating shared state without locking (per SDK source).

## Desired End State

Every `dataguard` command except `logout` requires a valid Supabase Auth session. On first
use (or when the cached session cannot be silently refreshed), the CLI prompts interactively
for email/password, then caches the session as JSON at `.dataguard_session` (mode `0600`) in
the working directory. Subsequent runs reuse the cache, silently refreshing an expired access
token via the refresh token when possible. `dataguard logout` clears the cache.

Verify by: running `dataguard sync ...` with no cached session prompts for login before any
other output; running it again immediately does not re-prompt; `dataguard logout` followed by
another run re-prompts; a wrong password produces a clear message and a non-zero exit code.

### Key Discoveries:

- `dataguard/cli.py:18-20` — the empty `@app.callback()` is where the gate belongs, but it
  must special-case `ctx.invoked_subcommand == "logout"` — otherwise an engineer whose
  session is unrefreshable can never reach the one command that clears it.
- `dataguard/watermark.py:9-54` — precedent for a local state file, but its format (bare ISO
  string) doesn't fit a multi-field session; the session cache needs JSON instead. Testing
  precedent (`test_watermark.py:14`, monkeypatch the module-level path constant) carries over
  directly.
- `dataguard/models.py:6-16` — dataclass convention uses `str | None`, not `typing.Optional`;
  a new `Session` model should match.
- supabase-py's default client's background auto-refresh thread is unsynchronized against
  main-thread session reads; since a `dataguard` run is short-lived (<60s NFR target), the
  thread outlives the run for no benefit and should be disabled in favor of an explicit,
  synchronous expiry check on startup.
- `dataguard/env_check.py` already establishes the "never print credential values" norm this
  change must extend to the password prompt and cached tokens.

## What We're NOT Doing

- Supabase Auth account provisioning or self-signup (`dataguard signup`). Accounts are
  assumed to already exist, created via the Supabase dashboard/invite by whoever administers
  the project (PRD Open Question #6 — resolution needed separately).
- Attaching the authenticated engineer's identity to Supabase rejection log rows. The PRD's
  "per-user attribution" rationale for per-engineer accounts is not realized by this change —
  wiring identity into `_write_rejections_to_supabase` is a separate follow-up.
- OAuth, phone, or magic-link sign-in — email/password only.
- Multi-account switching without an explicit `logout` — one cached session at a time.
- Any audit logging beyond the login/logout events' own console output.

## Implementation Approach

A new `dataguard/auth.py` module owns the full session lifecycle (login, cache read/write,
silent refresh, logout) as a self-contained, independently testable unit — mirroring the
separation `watermark.py` already provides for watermark state. CLI wiring is a thin adapter:
the Typer app callback calls into `auth.py` before any command runs (except `logout`), and a
new `dataguard logout` command calls `auth.clear_session()`. Keeping the SDK interaction
inside `auth.py` means `cli.py` and its tests never need to know about Supabase Auth
internals — only about the `Session`/`AuthError` contract.

## Critical Implementation Details

### State sequencing: the callback must exempt `logout`

The Typer app callback is the single gate applied to every command. If it blocks
unconditionally on a valid session, an engineer whose refresh token has expired or been
revoked can never reach `dataguard logout` to clear the stale cache — the callback must
inspect `ctx.invoked_subcommand` and skip the auth call when it is `"logout"`.

### Auto-refresh: disable the SDK's background thread

supabase-py's default client starts a `threading.Timer`-based auto-refresh daemon thread that
mutates session state without locking. A `dataguard` run is short-lived, so this thread adds a
real (if rare) race against the main thread's own session handling for no benefit. Construct
the auth client with auto-refresh disabled and perform the expiry-check-and-refresh explicitly
and synchronously at the start of `ensure_session()`.

## Phase 1: Auth core module

### Overview

Build `dataguard/auth.py` as a self-contained, testable module: a `Session` model, JSON cache
read/write at `.dataguard_session` with `0600` permissions, login via `sign_in_with_password`,
silent refresh via the cached refresh token, and session clearing. No CLI wiring in this phase.

### Changes Required:

#### 1. Session data model

**File**: `dataguard/models.py`

**Intent**: Add a `Session` dataclass representing a cached Supabase Auth session, following
the existing `RecordResult` dataclass convention.

**Contract**: `Session` carries `access_token: str`, `refresh_token: str`, and an expiry field
(`expires_at: datetime`, or an epoch `int` — whichever the SDK's `AuthResponse.session`
exposes most directly) sufficient to serialize to/from the JSON cache and determine expiry
without a network call.

#### 2. Auth module — cache, login, refresh, logout

**File**: `dataguard/auth.py` (new)

**Intent**: Own the full session lifecycle so `cli.py` never talks to the Supabase Auth SDK
directly, mirroring the separation `watermark.py` provides for watermark state.

**Contract**: Expose `ensure_session() -> Session` as the main entry point — reads the cache;
if the access token is expired but the refresh token is valid, refreshes silently; if no
usable session exists, prompts interactively (`typer.prompt(..., hide_input=True)`) for
email/password, logs in, and writes the resulting session to cache. Also expose
`load_cached_session() -> Session | None`, `save_session(session: Session) -> None` (writes
JSON then `os.chmod(path, 0o600)`), and `clear_session() -> None` (removes the cache file if
present, no error if absent). On any login/refresh failure, raise a typed `AuthError` with a
`reason: Literal["invalid_credentials", "network", "session_revoked"]` rather than a bare
exception or a direct `typer.Exit` — keeps the module framework-agnostic; `cli.py` translates
`AuthError` to the exit-code convention in Phase 2. Never print the password, access token, or
refresh token anywhere, matching the no-credential-leakage norm already enforced in
`env_check.py`.

#### 3. Ignore the session cache file

**File**: `.gitignore`

**Intent**: Prevent the session cache from ever being committed, matching the existing
treatment of `.env` and `.watermark`.

**Contract**: Add `.dataguard_session` under the existing "Runtime state — never commit"
section.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_auth.py -v`
- Type checking passes: `uv run mypy dataguard/`
- Linting passes: `uv run ruff check dataguard/`

#### Manual Verification:

- Deleting `.dataguard_session` and triggering `ensure_session()` prompts for credentials and
  creates the file with `0600` permissions (`ls -l .dataguard_session`)
- The cache file's contents contain only token/expiry fields — no plaintext password

---

## Phase 2: CLI integration

### Overview

Wire the auth gate into the Typer app callback so it runs before every command except
`logout`, add the `dataguard logout` command, and translate `AuthError` into the existing
`typer.Exit(code=1)` convention with distinct, actionable console messages.

### Changes Required:

#### 1. App-wide auth gate

**File**: `dataguard/cli.py`

**Intent**: Call `auth.ensure_session()` from the Typer app callback so every command is
gated, exempting `logout` per the Critical Implementation Details note above.

**Contract**: The callback inspects `ctx.invoked_subcommand`; when it is `"logout"`, skip the
auth call entirely; otherwise call `auth.ensure_session()`, catch `AuthError`, print a message
specific to `reason` ("invalid email or password" / "could not reach Supabase — check your
network connection" / "your session was revoked — please log in again"), and exit via
`typer.Exit(code=1)`, matching the pattern already used at cli.py:43/55/67/94.

#### 2. Logout command

**File**: `dataguard/cli.py`

**Intent**: Let an engineer clear their cached session on demand.

**Contract**: New `@app.command() def logout() -> None` calling `auth.clear_session()` and
printing a confirmation via the module's `console`.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_cli.py -v`
- Type checking passes: `uv run mypy dataguard/`
- Linting passes: `uv run ruff check dataguard/`

#### Manual Verification:

- Running `uv run dataguard sync ...` with no cached session prompts for login before any
  other output
- Running it again immediately succeeds without re-prompting
- Running `uv run dataguard logout` then `uv run dataguard sync ...` re-prompts for login
- Entering a wrong password shows the "invalid email or password" message and exits non-zero
  (`echo $?` shows `1`)

---

## Phase 3: Testing & leakage regression

### Overview

Cover the new auth module and CLI gate with unit tests mocking the Supabase Auth client, and
extend the existing no-leakage regression test to assert the session cache and console output
never contain the password, access token, or refresh token.

### Changes Required:

#### 1. Auth module unit tests

**File**: `tests/test_auth.py` (new)

**Intent**: Test `ensure_session`, `save_session`/`load_cached_session`, and `clear_session` in
isolation, mirroring `test_watermark.py`'s monkeypatch-the-module-constant style.

**Contract**: Monkeypatch `dataguard.auth._SESSION_PATH` to a `tmp_path` file and monkeypatch
the Supabase auth calls (`sign_in_with_password`, refresh) with fakes. Cover: no cache →
prompts and logs in; valid cached session → no prompt, no network call; expired access token +
valid refresh token → silent refresh, no prompt; expired refresh token → falls back to
interactive prompt; login failure → raises `AuthError` with the correct `reason`.

#### 2. CLI gate tests

**File**: `tests/test_cli.py`

**Intent**: Extend existing CLI tests to cover the callback's auth gate and the new `logout`
command, following the existing `unittest.mock.patch` pattern used for `env_check`/`run_sync`.

**Contract**: Patch `dataguard.cli.auth.ensure_session` to simulate success/`AuthError` and
assert the correct exit code and message per failure `reason`; patch
`dataguard.cli.auth.clear_session` for the `logout` command test; assert the callback does not
call `ensure_session` when the invoked subcommand is `logout`.

#### 3. No-leakage regression

**File**: `tests/test_no_leakage_regression.py`

**Intent**: Extend the existing leakage guard to the new credential surface.

**Contract**: Assert that no captured console output (login prompt, success/failure messages)
contains a literal password fixture value, and that `.dataguard_session` (read back in the
test) contains no plaintext password field — only token/expiry data.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest`
- Type checking passes: `uv run mypy dataguard/`
- Linting passes: `uv run ruff check dataguard/`

#### Manual Verification:

- Reading through `tests/test_auth.py` and `tests/test_cli.py` confirms no test hits a real
  Supabase Auth endpoint — every network call is mocked

---

## Testing Strategy

### Unit Tests:

- Auth module: cache hit/miss, silent refresh, refresh-failure fallback to prompt, login
  failure reasons, cache file permissions
- CLI: gate blocks/allows correctly, `logout` bypasses the gate, exit codes match failure
  reasons

### Integration Tests:

- None required — Supabase Auth is fully mocked for this change. If a future need arises for
  a live-account smoke test, use the existing `integration` pytest marker already registered
  in `conftest.py`, matching how DB integration tests are opted into.

### Manual Testing Steps:

1. Delete any existing `.dataguard_session`, run
   `dataguard sync --table users --rules rules/orders.json --dry-run`, confirm an interactive
   login prompt appears before any sync output.
2. Enter valid credentials; confirm the run proceeds and `.dataguard_session` is created with
   `0600` permissions.
3. Run the same command again immediately — confirm no login prompt (session reused).
4. Run `dataguard logout`; confirm the cache file is removed and a confirmation prints.
5. Run `dataguard sync ...` again — confirm it re-prompts for login.
6. Enter an intentionally wrong password — confirm the "invalid email or password" message and
   a non-zero exit code.

## Performance Considerations

Silent token refresh adds one network round-trip to Supabase only on runs where the cached
access token has expired — negligible against the 60s/10k-record throughput NFR. A run with a
still-valid cached access token makes no auth network call at all.

## Migration Notes

No data migration. `.dataguard_session` is added to `.gitignore` in Phase 1 alongside the
existing `.env`/`.watermark` entries.

## References

- PRD: `context/foundation/prd.md` §Access Control, FR-009, FR-010, Open Questions #6-7
- Local-state-file precedent: `dataguard/watermark.py`
- Credential-handling precedent: `dataguard/env_check.py`
- CLI exit-code convention: `dataguard/cli.py:43,55,67,94`
- Supabase client precedent: `dataguard/sync.py:151-157` (`_connect_supabase`)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Auth core module

#### Automated

- [ ] 1.1 Unit tests pass: `uv run pytest tests/test_auth.py -v`
- [x] 1.2 Type checking passes: `uv run mypy dataguard/` — 29bab9d
- [x] 1.3 Linting passes: `uv run ruff check dataguard/` — 29bab9d

#### Manual

- [ ] 1.4 Deleting `.dataguard_session` and triggering `ensure_session()` prompts for credentials and creates the file with `0600` permissions
- [ ] 1.5 The cache file's contents contain only token/expiry fields — no plaintext password

### Phase 2: CLI integration

#### Automated

- [x] 2.1 Unit tests pass: `uv run pytest tests/test_cli.py -v`
- [x] 2.2 Type checking passes: `uv run mypy dataguard/`
- [x] 2.3 Linting passes: `uv run ruff check dataguard/`

#### Manual

- [ ] 2.4 Running `uv run dataguard sync ...` with no cached session prompts for login before any other output
- [ ] 2.5 Running it again immediately succeeds without re-prompting
- [ ] 2.6 Running `uv run dataguard logout` then `uv run dataguard sync ...` re-prompts for login
- [ ] 2.7 Entering a wrong password shows the "invalid email or password" message and exits non-zero

### Phase 3: Testing & leakage regression

#### Automated

- [ ] 3.1 Full test suite passes: `uv run pytest`
- [ ] 3.2 Type checking passes: `uv run mypy dataguard/`
- [ ] 3.3 Linting passes: `uv run ruff check dataguard/`

#### Manual

- [ ] 3.4 Reading through `tests/test_auth.py` and `tests/test_cli.py` confirms no test hits a real Supabase Auth endpoint
