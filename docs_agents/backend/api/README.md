# Backend API layer (`backend/src/api/`, `backend/src/main.py`)

> Owner role: **backend** · HTTP spec for the operations defined here: [`openapi.yaml`](openapi.yaml) (generated, 17 operations) · service APIs: [service catalog](../README.md#service-catalog) · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified

## Purpose

Application composition, authentication endpoints, user management, small common utilities, internal cross-service endpoints, and the dependency-injection helpers every router uses. Service-specific routes live in each service package (`backend/src/services/<name>/routes*`), not here.

## Composition **[S][V]**

`backend/src/main.py` creates `app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)`. It mounts `/content` (`ContentStaticFiles`) and includes 13 routers, in this order:

1. `api/v1/routes_auth`, `routes_common`, `routes_users` (prefix `settings.API_V1_STR` = `/api/v1`)
2. `api/internal/jobs_routes`, `services_routes` (prefix `/internal`)
3. service routers: `logging`, `content`, `configuration`, `text_translation`, `text_to_speech`, `text_to_image`, `voice_optimization`, `evaluation`

The lifespan does the following:

- initializes the users DB;
- builds the registry and loads configs;
- configures logging;
- creates the in-memory `JobStore` (TTL 3600 s);
- signs a 24 h internal JWT and configures `log_client`;
- starts the config hot-reload and job-eviction tasks;
- in `DEBUG_MODE`, or on Windows, overrides `require_uds` (and, in `DEBUG_MODE`, `require_admin`) with no-ops.

The runtime surface is **91 operations on 70 paths**. Every one is owned by exactly one generated document under `docs_agents/backend/` **[V]**.

## Dependencies (`api/dependencies.py`) **[S]**

| Dependency | Grants |
| --- | --- |
| `get_current_user` (→ `security.auth.verify_token`) | Any valid bearer token. **Returns an admin identity for everyone when `AUTH_DISABLED=True`** (the default) |
| `require_write_access` | All roles may `GET`/`HEAD`/`OPTIONS`; other methods need `admin` or `internal` |
| `require_admin` | Role `admin` |
| `require_internal` | Role `internal` or `admin` |
| `require_uds` | Requests that arrived over the Unix domain socket (`scope["server"][0]` is a path) |
| `get_registry`, `get_registry_builder`, `get_job_store`, `get_internal_token` | Objects from `app.state` |

`api/service_resolver.py`: `get_typed_service(registry, service_name, expected_type)` and `require_active_providers(service, …)` (503 when a service has no active provider).

## Operations defined in this layer

| Operation | Access (declared) | Notes |
| --- | --- | --- |
| `GET /` | public | Welcome message |
| `POST /api/v1/login` | public | Form login → JWT (`ACCESS_TOKEN_EXPIRE_MINUTES`, default 30); 401 on bad credentials |
| `GET /api/v1/auth/config` | public | Tells the login page whether Google sign-in is allowed |
| `POST /api/v1/login/google` | public | Google ID token → JWT; 403 when disabled, 401 invalid, 503 verifier unavailable; unknown emails are registered as `viewer` |
| `POST /api/v1/cli-login` | public, **UDS checked in the handler** | Short-lived CLI JWT (`CLI_TOKEN_EXPIRE_SECONDS`, default 30); 403 over TCP. The handler check is *not* bypassed by the `DEBUG_MODE` dependency override |
| `GET /api/v1/ping` | public | Health text with platform |
| `GET /api/v1/status` | any authenticated | Status plus all settings, with secrets masked (`core/tasks.py`) |
| `POST /api/v1/list-files` | any authenticated | Lists the content directory down to body `depth` folder levels as relative paths, folders ending in `/` (read-only; allowed for viewers) |
| `POST /api/v1/operation` | write access | Placeholder that echoes success |
| `GET`/`POST /api/v1/users`, `DELETE /api/v1/users/{username}`, `PATCH …/{username}/password`, `PATCH …/{username}/role` | **UDS + admin** (router-level dependencies) | Roles restricted to `admin`/`viewer` by schema pattern; 404 unknown user; 409 conflicts |
| `POST /internal/generate`, `GET /internal/generate/{job_id}/status` | internal | Async TTS pipeline job. **`POST` is broken**: `_run_job` is commented out, so the endpoint raises `NameError` after saving the upload (discovery F-1) |
| `GET /internal/services` | internal | All registered services' provider infos |

## Security notes

- The declared access model above is only effective when `AUTH_DISABLED=False` and `DEBUG_MODE=False`. Both default to `True` (discovery SEC-1, SEC-2); see [security](../security/README.md).
- New mutating routes must use `require_write_access`, `require_admin` or `require_internal`. `src/api/__tests__/test_viewer_access.py` enumerates every non-read operation from `app.openapi()` and asserts that viewers get 403 (65 tests) **[V]**.

## Testing

| Command | Scope | Baseline |
| --- | --- | --- |
| `cd backend && ../.venv/bin/python -m pytest -q src/api` | `api/__tests__/test_viewer_access.py` (65), `api/v1/__tests__/test_routes_auth_google.py` (23) | 88 passed in the 919-test baseline **[V]** |

The users routes, `/internal/generate*` and `/internal/services` have no dedicated tests.
