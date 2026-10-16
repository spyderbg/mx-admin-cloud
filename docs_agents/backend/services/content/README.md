# `content` service

> Registered service · `SERVICE_NAME = "content"` · owner role: **backend** · HTTP spec: [`openapi.yaml`](openapi.yaml) (generated) · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../../../discovery/README.md)
>
> Not listed in the v3 assignment's service inventory, but included in v4's (§2.1). This is the largest service and the only one with a public `/api/v1` surface (decision D-1, approved: documented as in scope).

## Purpose and Usage

Content management for the Kiddo app:

- Categories: localized names and descriptions, icon, images, per-language voices, archive / restore / permanent delete.
- Items inside a category: localized names, image variants with a main image, voice takes per language with a main voice, optimized voice variants.

Generation steps are delegated over HTTP to the translation, text-to-speech, text-to-image and voice-optimization services. Examples: creating an item can translate its name and optionally generate its image and voices, and a voice can be regenerated or optimized.

Consumers: the frontend (generated client `frontend/src/api/contentClient.ts` + `cache.ts`) and the CLI `content` group. Media files are served by the `/content` static mount.

## Location and Dependencies

| Path | Role |
| --- | --- |
| [`backend/src/services/content/__init__.py`](../../../../backend/src/services/content/__init__.py) | Registration (`config_subdir="content"`) |
| [`service.py`](../../../../backend/src/services/content/service.py) | `ContentService`, `ContentProviderFactory` |
| [`models.py`](../../../../backend/src/services/content/models.py) | `ContentProvider` contract (`:299`), result dataclasses, error hierarchy |
| [`providers/file_system/`](../../../../backend/src/services/content/providers/file_system/) | `FileSystemContentProvider` = `FileSystemCategoriesMixin` (`categories.py`) + `FileSystemItemsMixin` (`items.py`) + `FileSystemInternalApiMixin` (`internal_api.py`); CSV registries in `registry.py`; `constants.py` (`CreationMethod`, sidecar columns) |
| [`routes/`](../../../../backend/src/services/content/routes/) | `__init__.py` (router, prefix `/api/v1/content`, `dependencies=[Depends(require_write_access)]`), `categories.py`, `items.py`, `common.py`, `static_files.py` (`ContentStaticFiles`) |
| [`schemas.py`](../../../../backend/src/services/content/schemas.py) | Pydantic request/response models (input to the generated `frontend/src/types/contentSchemas.ts`) |

Runtime HTTP dependencies, called by `FileSystemInternalApiMixin` with a 30-second internal JWT against `settings.API_BASE_URL` **[S]**:

- `POST /internal/translators/translate`
- `POST /internal/generators/generate-word`
- `GET /internal/optimizers/` and `POST /internal/optimizers/{optimizer}/optimize`
- `GET /internal/configurations/voice_optimization/strategy`
- `POST /internal/image-generators/generate-image`

Filesystem: `settings.content_path` (`CONTENT_DIR`, default `../content`) and `settings.archive_path` (`ARCHIVE_DIR`).

## Service Interface / ServiceProvider Contract

**Service:** `ContentService(Service)` — `service.py:47`; `SUPPORTED_STRATEGIES = (FIRST_SUCCESS,)`, default `FIRST_SUCCESS`. Every public method runs the corresponding provider operation on the active providers in priority order until one succeeds (`_first_success`). All methods are **synchronous** (FastAPI runs them in its threadpool) **[V][S]**.

| Area | `ContentService` methods |
| --- | --- |
| Categories | `list_categories(include_archived=False, archived_only=False)`, `category_details_by_name(category_name)`, `category_details_by_id(category_id)`, `create_category(category_name)`, `update_category_details(category_id, category_name, description)`, `update_category_id(category_id, new_category_id)`, `archive_category(category_name)`, `remove_category(category_name)`, `remove_category_by_id(category_id)`, `restore_category(category_name)`, `delete_archived_category(archive_name)` |
| Category media | `add_category_image`, `update_category_image`, `delete_category_image`, `set_category_icon`, `add_category_voice`, `update_category_voice`, `delete_category_voice`, `regenerate_category_voice` |
| Items | `create_item_by_id(category_id, english_word, image_data=None, image_extension=".png", generate_voices=False, generate_image=False)`, `update_item`, `update_item_id`, `remove_item` |
| Item images | `item_images`, `add_item_image`, `generate_item_image(category_id, item_id, prompt, configuration_name=None)`, `update_item_image`, `delete_item_image`, `set_main_item_image` |
| Item voices | `item_voices`, `add_item_voice`, `update_item_voice`, `delete_item_voice`, `regenerate_item_voice`, `generate_item_voice(category_id, item_id, request: GenerateItemVoice)`, `set_main_item_voice`, `optimize_item_voice` |

**Provider contract:** `ContentProvider(ServiceProvider, ABC)` — `models.py:299`, "Provider contract for content CRUD operations". It declares 33 abstract methods mirroring the table above. Provider-side names differ in three places: `category_details(category_id)`, `create_item(...)` and `update_item_name(...)`. It also adds `category_id_by_name(category_name)` **[V]**.

## Implemented Providers

| Class | Registered name | Path | Capabilities |
| --- | --- | --- | --- |
| `FileSystemContentProvider` | `file_system` | `providers/file_system/provider.py:39` | Full contract over the content directory tree (see below) |

File-system model **[S]** (details in `backend/CLAUDE.md`, verified against source):

- Each category's folder holds `items_ids.csv` + `items_<lang>.csv`. The content root holds `categories_ids.csv`, `categories_<lang>.csv` and `descriptions_<lang>.csv`. These are row-aligned registries, read and written only through `registry.py` (`CATEGORIES`, `ITEMS`).
- Registry access holds a **process-wide re-entrant lock** plus an **`fcntl.flock` file lock** (`.<name>_registry.lock`). Writes are staged to `.tmp` files and committed with a `.<name>_registry.commit` marker, so an interrupted write is completed or discarded on the next access.
- Media sidecars: a one-row `<stem>.csv` with `creation_method` (`uploaded`/`generated`/`optimized`/`unknown`), `provider`, `parameters`, `hash`.
- Deletes first rename the folder to a hidden `.<name>.deleting`. Archived categories go to `archive/<category>_<unix_timestamp>/`.

## Configured Provider Instances

| File | Provider | Enabled | Priority | `config` |
| --- | --- | --- | --- | --- |
| [`backend/config/content/file_system.json`](../../../../backend/config/content/file_system.json) | `file_system` | yes | 1 | `{}` (paths come from settings, not the file) |

**[V]**

## Selection and Lifecycle

- `FIRST_SUCCESS` over active providers in ascending priority. With one provider, that provider answers or raises.
- When **no content provider is active**:
  - every content route answers **503** (`get_content_service` → `require_active_providers`);
  - the `/content` static mount also answers 503 (`ContentStaticFiles.__call__`).

  The frontend relies on this to show "content unavailable" **[S]**.
- Providers are reloaded with the registry. A disabled `file_system.json` therefore turns the content API off at runtime.

## HTTP API

37 operations under `/api/v1/content`, plus the `/content/**` static mount (not part of the OpenAPI document). Every operation passes `require_write_access`: viewers may `GET`, while only `admin`/`internal` may change data. `api/__tests__/test_viewer_access.py` guards this **[V]**. Full contract: [`openapi.yaml`](openapi.yaml).

| Resource | Operations |
| --- | --- |
| Categories | `GET`/`POST /categories`, `POST /categories/restore`, `DELETE /categories/archive/{archive_name}`, `GET`/`PATCH`/`DELETE /categories/{category_id}` |
| Category media | `POST`/`DELETE …/icon`; `GET`/`POST …/images`, `PUT`/`DELETE …/images/{image_id}`; `GET`/`POST …/voices`, `PUT`/`DELETE …/voices/{voice_id}`, `POST …/voices/{voice_id}/regenerate` |
| Items | `GET`/`POST …/items`, `GET`/`PUT`/`DELETE …/items/{item_id}` |
| Item images | `GET`/`POST …/images`, `POST …/images/generate`, `PUT`/`DELETE …/images/{image_id}`, `PUT …/images/{image_id}/main` |
| Item voices | `GET`/`POST …/voices`, `POST …/voices/generate`, `PUT`/`DELETE …/voices/{voice_id}`, `PUT …/voices/{voice_id}/main`, `POST …/voices/{voice_id}/optimize`, `POST …/voices/{voice_id}/regenerate` |

## Errors, Observability and Security

- Errors (`models.py`): `ContentError` → `ContentProviderUnavailableError`, `InvalidCategoryNameError`, `CategoryNotFoundError`, `CategoryConflictError`, `ArchiveUnavailableError`, `ContentOperationError` (incl. `RegistryCommitPending` in `registry.py`).
- Routes map these mainly to 400, 404, 409 and 500. Missing providers map to 503. Empty uploads map to 400 **[S]**.
- Observability: provider and service events go over HTTP via `core/log_client` (the logging rule in `backend/CLAUDE.md`).
- Security:
  - Every new mutating route must keep `require_write_access`, because Google sign-in registers unknown users as viewers.
  - Uploads write files into the real content tree.
  - Generation endpoints trigger paid external providers.
  - The content tree is tracked in Git and treated as **production-like data**: agents must not modify it (architecture §1).

## Testing

| Command | Scope | Baseline |
| --- | --- | --- |
| `cd backend && ../.venv/bin/python -m pytest -q src/services/content` | 12 files, 474 tests (`test_content_service*.py`, `test_content_filesystem_*.py`, `test_content_access.py`, `test_content_static_files.py`) | Passed as part of the 919-test baseline **[V]** |

Fixtures:

- `__tests__/conftest.py`: temp content trees; sets `AUTH_DISABLED=False` for the 401 paths.
- `content_fs_helpers.py`, `content_http_helpers.py`.
- `__tests__/http/*.http`: manual REST-client requests.

Known gap: `test_content_filesystem_item_images.py` creates registry lock files in the **real** `content/` tree (discovery F-5).
