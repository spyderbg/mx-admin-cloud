# Backend security (`backend/src/security/`, `backend/src/db/`, auth dependencies)

> Owner role: **backend** · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified · no secret values appear in this document

## Components

| Module | Responsibility |
| --- | --- |
| [`security/auth.py`](../../../backend/src/security/auth.py) | `verify_token`: decodes the bearer JWT (`SECRET_KEY`, `ALGORITHM`) and returns `{"username", "role"}`; 401 when missing or invalid. **Short-circuits to an admin identity when `settings.AUTH_DISABLED` is true** |
| [`security/tokens.py`](../../../backend/src/security/tokens.py) | `create_access_token(data, expires_delta=None)`; `create_internal_token(service_name, expires_delta)` with role `internal` |
| [`security/google.py`](../../../backend/src/security/google.py) | Verification of Google ID tokens for "Log in with Google" |
| [`db/database.py`](../../../backend/src/db/database.py) | `UsersDb`: SQLite users table; passwords use a SHA-256 pre-hash, then bcrypt; seeds `admin` with `DEFAULT_ADMIN_PASSWORD` when absent; `get_or_register_email_user` registers Google users as `viewer` |
| [`api/dependencies.py`](../../../backend/src/api/dependencies.py) | Role and transport dependencies (see [API layer](../api/README.md#dependencies-apidependenciespy-s)) |

## Tokens and roles

| Token | Issued by | Lifetime | Role |
| --- | --- | --- | --- |
| User JWT | `POST /api/v1/login`, `POST /api/v1/login/google` | `ACCESS_TOKEN_EXPIRE_MINUTES` (30) | `admin` or `viewer` |
| CLI JWT | `POST /api/v1/cli-login` (UDS only) | `CLI_TOKEN_EXPIRE_SECONDS` (30 s) | user's role |
| Startup internal JWT | `main.py` lifespan | 24 h | `internal` |
| Per-call internal JWT | content provider → internal APIs | 30 s | `internal` |

`internal` is accepted by `require_internal` and `require_write_access`. Viewers are read-only on content and are refused on internal and user routes.

## Insecure defaults (verified in code; deployment state unknown)

| Setting (`core/config.py`) | Default | Effect when not overridden |
| --- | --- | --- |
| `AUTH_DISABLED` | `True` | Every request, including one **without a token**, is treated as `admin` |
| `DEBUG_MODE` | `True` | `require_uds` and `require_admin` are replaced with no-ops in the lifespan (user management over TCP, no admin checks) |
| `SECRET_KEY` | placeholder literal | Forgeable JWTs if the default is used. Docker Compose passes `${SECRET_KEY}`, which becomes an empty string when the variable is unset |
| `DEFAULT_ADMIN_PASSWORD` | hard-coded literal | Seeded admin password on a fresh DB; the same literal is the CLI password default in `docker/docker-compose.yml` |

No tracked deployment artifact sets these to safe values (discovery SEC-1..SEC-4). Whether the deployment host's `.env` does is **[U]**. The operator should verify it without sharing values (discovery U-1).

## Sensitive state

- `backend/data/users.db` and `data/users.db` are **tracked in git** and contain password hashes. `DB_PATH` is relative and resolved against the process working directory, so the effective DB depends on where uvicorn starts **[V][S]**.
- Rule for all agents: never read out, reset, seed, migrate or overwrite these files. Tests must use temporary DBs, as `test_routes_auth_google.py` does with `tmp_path`.

## Guards that exist

- `src/api/__tests__/test_viewer_access.py` — viewers get 403 on every mutating operation **[V]**.
- Integration tests set `AUTH_DISABLED=False` with `monkeypatch` to exercise 401/403 paths (`configuration`, `content`, `text_to_image`, `text_to_speech`, `text_translation`) **[S]**.
- `core/tasks.status()` masks secret-like settings **[S]**.

## Open issues (Backend-owned; not changed in Phase 1)

SEC-1..SEC-4 (secure-by-default settings), SEC-5 (DB files in git), SEC-8 (filesystem paths accepted from request bodies in evaluation and voice optimization). Operator decision D-5 sets their priority.
