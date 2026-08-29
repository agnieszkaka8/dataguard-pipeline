# Supabase Auth Login — Plan Brief

> Full plan: `context/changes/auth-supabase-login/plan.md`

## What & Why

DataGuard currently has no authentication — anyone who can read the local `.env` file can run
the full sync pipeline (this was an explicit PRD design choice for MVP). This change adds
per-engineer login via Supabase Auth, gating every `dataguard` command behind a valid session,
so access is tied to an individual identity rather than a shared secret file.

## Starting Point

`dataguard/cli.py` has one command (`sync`) and an empty Typer app callback. The only local
state file precedent is `.watermark` (a bare timestamp string). Supabase is already a
dependency, already used to write the rejection log via a service-role key in `.env` — but
never used for user authentication. No login/session code exists anywhere in the codebase.

## Desired End State

Running any `dataguard` command without a valid cached session prompts the engineer for
Supabase Auth email/password. A successful login is cached locally and silently reused/
refreshed on later runs — no repeated prompts. `dataguard logout` clears the cache and forces
re-login. Wrong credentials or an unreachable Supabase produce a clear message and a non-zero
exit, consistent with every other failure mode in the CLI.

## Key Decisions Made

| Decision                          | Choice                                                | Why (1 sentence)                                                                 | Source |
| ---------------------------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------- | ------ |
| PRD conflict (Access Control said "no auth") | Amend the PRD before planning | The original Non-Goal is a real, documented product decision — can't be silently overridden. | Plan |
| Gate scope                         | Every `dataguard` invocation                          | Matches FR-009; one gate, one mental model.                                       | Plan   |
| Session persistence                | Interactive login + cached local session               | Avoids re-prompting every run while keeping the CLI's existing `.env`-free auth flow. | Plan   |
| Account model                      | Per-engineer Supabase Auth accounts                    | Enables future per-user attribution (not built in this change, see below).        | Plan   |
| Cache file                         | JSON at `.dataguard_session` in cwd, mode `0600`        | Mirrors the existing `.watermark` precedent; restrictive perms protect a live credential. | Plan   |
| SDK auto-refresh thread            | Disabled; refresh manually and synchronously on startup | The default background thread is unsynchronized and pointless for a <60s CLI run. | Plan   |
| Expired access token handling      | Silent refresh via refresh token before re-prompting    | Realizes FR-010's "avoid repeated prompts" goal.                                  | Plan   |
| Account provisioning               | Out of scope — accounts assumed to already exist        | Keeps this plan focused on the CLI-side auth flow, not Supabase project admin.    | Plan   |
| Engineer identity → rejection log  | Out of scope for this change                            | The PRD's "attribution" rationale isn't realized yet — flagged as a follow-up, not silently dropped. | Plan |
| Logout command                     | Added (`dataguard logout`), flagged nice-to-have if cut | Deleting the gate would otherwise require knowing an internal filename.           | Plan   |
| Auth failure messaging             | Distinct message per cause, same exit code (1)          | Actionable for the engineer; scripts still only need to check for non-zero.       | Plan   |
| Testing approach                   | Mock the Supabase Auth client                           | Matches the existing mock-based test style (`test_watermark.py`); no live-account dependency in CI. | Plan |

## Scope

**In scope:**
- Interactive email/password login via Supabase Auth
- Local session cache with silent refresh
- Auth gate applied to every CLI command (except `logout`)
- `dataguard logout` command
- PRD amendment (Access Control, FR-009/FR-010)

**Out of scope:**
- Account provisioning / self-signup
- Attaching engineer identity to rejection log rows (a follow-up change)
- OAuth / magic-link / phone sign-in
- Multi-account switching without logout

## Architecture / Approach

A new `dataguard/auth.py` module owns the full session lifecycle (login, cache, refresh,
logout) independently of the CLI, mirroring how `watermark.py` owns watermark state. `cli.py`
becomes a thin adapter: the Typer app callback calls `auth.ensure_session()` before any
command (except `logout`), and translates a typed `AuthError` into the CLI's existing
`typer.Exit(code=1)` convention.

## Phases at a Glance

| Phase                          | What it delivers                                              | Key risk                                                        |
| ------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------ |
| 1. Auth core module             | `dataguard/auth.py`: session model, cache, login, refresh, logout | Getting the SDK's session/refresh contract right without a live test project |
| 2. CLI integration              | App-wide gate in the Typer callback + `dataguard logout`         | The `logout`-exemption logic — get it wrong and engineers get locked out |
| 3. Testing & leakage regression | Unit tests (mocked SDK) + extended no-leakage regression test    | Ensuring the mocks accurately reflect real SDK behavior            |

**Prerequisites:** Supabase Auth must be enabled on the project, and at least one test
engineer account must exist for manual verification (provisioning is out of scope for the
code in this plan, but someone needs to have created an account before Phase 1's manual steps
can be verified).
**Estimated effort:** ~3 sessions across 3 phases.

## Open Risks & Assumptions

- The exact supabase-py session-restore API (equivalent to `set_session`) wasn't confirmed via
  documentation lookup for the installed version (`supabase>=2.25.1`) — the implementer should
  verify the precise method name/signature against the installed package before writing
  `auth.py`, though the overall approach (store tokens, restore via SDK call, refresh via
  refresh token) is standard across Supabase client versions.
- Session cache is scoped to the working directory (like `.watermark`), so an engineer running
  `dataguard` from multiple directories logs in separately in each — acceptable given the
  existing watermark precedent already has this property.
- This change does not deliver the "per-user attribution" benefit the PRD's Access Control
  section cites as the rationale for per-engineer accounts; that requires a follow-up change
  to `_write_rejections_to_supabase`.

## Success Criteria (Summary)

- An engineer with no cached session is prompted to log in before any `dataguard` command
  runs, and is never prompted again until the session expires past what a refresh can recover.
- `dataguard logout` reliably clears the session and forces re-login on the next run, even if
  the prior session was invalid or revoked.
- No password, access token, or refresh token ever appears in console output, logs, or error
  messages — verified by an automated regression test.
