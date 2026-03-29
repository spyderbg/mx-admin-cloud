# Required Frontend API Endpoints

Every endpoint the frontend SPA needs to replace mock data with live API calls.
All paths are relative to the API base `{{baseUrl}}/api/v1` unless noted otherwise.
Authenticated endpoints require `Authorization: Bearer <token>`.

Supported languages: `en`, `es`, `fr`, `bg`
([`frontend/src/types/index.ts:1-3`](../frontend/src/types/index.ts#L1-L3)).

Frontend type definitions used throughout:
[`Category`](../frontend/src/types/index.ts#L23-L30),
[`Image`](../frontend/src/types/index.ts#L15-L21),
[`Voice`](../frontend/src/types/index.ts#L5-L13),
[`User`](../frontend/src/types/index.ts#L32-L36).

---

## 1. Authentication

### 1.1 `POST /api/v1/login`

| | |
|---|---|
| **Purpose** | Authenticate a user and obtain a JWT access token. |
| **When called** | User submits the login form. |
| **Backend status** | EXISTS |

**Frontend code:**

- API function: [`login()`](../frontend/src/api/auth.ts#L10-L20) in `api/auth.ts`
- Called from: [`handleLogin()`](../frontend/src/components/LoginForm.vue#L76-L85) in `LoginForm.vue`
- Router guard checks `localStorage.getItem('token')`:
  [`router/index.ts:40-47`](../frontend/src/router/index.ts#L40-L47)

**Backend code:**

- Route handler: `backend/src/api/v1/routes_auth.py`

**Request:**

```
Content-Type: application/x-www-form-urlencoded

username=admin&password=secret
```

**Response (200):**

```json
{ "access_token": "eyJhbG...", "token_type": "bearer" }
```

---

### 1.2 `GET /api/v1/me`

| | |
|---|---|
| **Purpose** | Return the authenticated user's profile (name, role). |
| **When called** | After login; populates navbar and sidebar user section on every authenticated page. |
| **Backend status** | MISSING |

**Frontend code (currently uses mock):**

- Sidebar profile: [`AppSidebar.vue:5-16`](../frontend/src/layouts/AppSidebar.vue#L5-L16) — displays
  `user.name` and `user.role` from
  [`mockUser`](../frontend/src/data/mockData.ts#L6-L10)
- Navbar user dropdown:
  [`AppNavbar.vue:48-62`](../frontend/src/layouts/AppNavbar.vue#L48-L62) — displays avatar, name,
  role from `mockUser`
- Logout (client-only): [`AppNavbar.vue:95-98`](../frontend/src/layouts/AppNavbar.vue#L95-L98)

**Expected response (200):**

```json
{
  "username": "admin",
  "role": "admin"
}
```

---

## 2. Categories

### 2.1 `GET /api/v1/content/categories`

| | |
|---|---|
| **Purpose** | List all categories with their images and voices — the primary data load for the app. |
| **When called** | On initial page load after login; populates the sidebar tree and the main categories view. |
| **Backend status** | EXISTS (but response shape needs enrichment — see note) |

**Frontend code (currently uses mock):**

- Sidebar navigation tree: [`AppSidebar.vue:44-80`](../frontend/src/layouts/AppSidebar.vue#L44-L80)
  — iterates `filteredCategories` derived from
  [`mockCategories`](../frontend/src/data/mockData.ts#L12-L297)
- Categories view: [`CategoriesView.vue:38-42`](../frontend/src/views/CategoriesView.vue#L38-L42)
  — finds category by route param from `mockCategories`

**Backend code:**

- Route: [`routes_content.py:37-47`](../backend/src/api/v1/routes_content.py#L37-L47)
- Service: [`content_service.py:119-143`](../backend/src/core/content_service.py#L119-L143)

**Note:** The current backend returns `{ "categories": ["animals", "foods"] }` (folder name
strings). The frontend `Category` type requires `id`, `name`, `description`, `iconUrl`, `voices[]`,
and `images[]`. The backend must return richer objects, or the frontend must hydrate each category
via endpoint 2.3.

**Expected response (200):**

```json
{
  "categories": [
    {
      "id": "nature_scenes",
      "name": "Nature Scenes",
      "description": "Mountains and Trees",
      "iconUrl": "/content/nature_scenes/nature_scenes.png",
      "voices": [
        {
          "id": "nature_scenes_en",
          "name": "Nature Scenes",
          "language": "en",
          "type": "tts",
          "audioUrl": "/content/nature_scenes/nature_scenes_en.mp3"
        }
      ],
      "images": [
        {
          "id": "mountain_peak",
          "name": "Mountain Peak",
          "thumbnailUrl": "/content/nature_scenes/mountain_peak/01.png",
          "files": ["/content/nature_scenes/mountain_peak/01.png"],
          "voices": [
            {
              "id": "mountain_peak_en",
              "name": "Mountain Peak",
              "language": "en",
              "type": "tts",
              "audioUrl": "/content/nature_scenes/mountain_peak/mountain_peak_en_01.mp3"
            }
          ]
        }
      ]
    }
  ]
}
```

---

### 2.2 `POST /api/v1/content/categories`

| | |
|---|---|
| **Purpose** | Create a new category (folder, DALL-E icon, per-language voice files). |
| **When called** | User clicks an "Add Category" button (not yet wired in UI). |
| **Backend status** | EXISTS |

**Backend code:**

- Route: [`routes_content.py:50-58`](../backend/src/api/v1/routes_content.py#L50-L58)
- Service: [`content_service.py:196-236`](../backend/src/core/content_service.py#L196-L236)

**Request:**

```json
{ "name": "animals" }
```

**Response (201):**

```json
{ "category": "animals", "message": "Category 'animals' created." }
```

---

### 2.3 `GET /api/v1/content/categories/{category_name}`

| | |
|---|---|
| **Purpose** | Get full details for one category — object folders, CSV word lists per language. |
| **When called** | When user selects a category in the sidebar; loads the category detail panel and image grid. |
| **Backend status** | EXISTS (response shape needs enrichment — see 2.1 note) |

**Frontend code (currently uses mock):**

- Category detail + image grid:
  [`CategoriesView.vue:2-21`](../frontend/src/views/CategoriesView.vue#L2-L21) — renders
  `CategoryDetail` and `ImageGrid` with the matched category
- Category detail card:
  [`CategoryDetail.vue:1-91`](../frontend/src/components/CategoryDetail.vue#L1-L91) — displays
  name, icon, description, voice cards

**Backend code:**

- Route: [`routes_content.py:102-110`](../backend/src/api/v1/routes_content.py#L102-L110)
- Service: [`content_service.py:146-179`](../backend/src/core/content_service.py#L146-L179)

**Response (200):**

```json
{
  "name": "animals",
  "objects": ["cat", "dog"],
  "images_en": ["cat", "dog"],
  "images_es": ["gato", "perro"],
  "images_fr": ["chat", "chien"],
  "images_bg": ["котка", "куче"],
  "images_names": ["cat", "dog"]
}
```

---

### 2.4 `PATCH /api/v1/content/categories/{category_name}`

| | |
|---|---|
| **Purpose** | Rename a category (moves folder, regenerates voice files, renames icon). |
| **When called** | User clicks the pencil icon next to the category name, types a new name, and confirms. |
| **Backend status** | EXISTS |

**Frontend code:**

- Rename modal trigger:
  [`CategoryDetail.vue:11-13`](../frontend/src/components/CategoryDetail.vue#L11-L13) — pencil
  button calls `openRenameModal()`
- Rename modal UI:
  [`CategoryDetail.vue:93-119`](../frontend/src/components/CategoryDetail.vue#L93-L119) —
  `confirmRename()` emits `rename` event
- Handler: [`CategoriesView.vue:56-58`](../frontend/src/views/CategoriesView.vue#L56-L58) —
  `onRenameCategory()` (currently `console.log`)

**Backend code:**

- Route: [`routes_content.py:76-86`](../backend/src/api/v1/routes_content.py#L76-L86)
- Service: [`content_service.py:383-442`](../backend/src/core/content_service.py#L383-L442)

**Request:**

```json
{ "name": "new_category_name" }
```

**Response (200):**

```json
{ "category": "new_category_name", "message": "Category renamed from 'old' to 'new_category_name'." }
```

---

### 2.5 `DELETE /api/v1/content/categories/{category_name}`

| | |
|---|---|
| **Purpose** | Delete (or archive) a category and all its contents. |
| **When called** | User clicks the delete icon on the category icon, then confirms in the modal. |
| **Backend status** | EXISTS |

**Frontend code:**

- Delete button:
  [`CategoryDetail.vue:33-39`](../frontend/src/components/CategoryDetail.vue#L33-L39) — opens
  delete confirmation modal
- Delete modal:
  [`CategoryDetail.vue:121-138`](../frontend/src/components/CategoryDetail.vue#L121-L138) — emits
  `delete` event
- Handler: [`CategoriesView.vue:53-55`](../frontend/src/views/CategoriesView.vue#L53-L55) —
  `onDeleteCategory()` (currently `console.log`)

**Backend code:**

- Route: [`routes_content.py:89-99`](../backend/src/api/v1/routes_content.py#L89-L99)
- Service: [`content_service.py:445-478`](../backend/src/core/content_service.py#L445-L478)

**Response (200):**

```json
{
  "archived": true,
  "name": "animals",
  "archive_folder": "animals_1738456789",
  "message": "Category 'animals' has been archived."
}
```

---

### 2.6 `POST /api/v1/content/categories/restore`

| | |
|---|---|
| **Purpose** | Restore an archived category back to the content directory. |
| **When called** | User triggers restore from an archived-categories panel (not yet wired in UI). |
| **Backend status** | EXISTS |

**Backend code:**

- Route: [`routes_content.py:61-73`](../backend/src/api/v1/routes_content.py#L61-L73)
- Service: [`content_service.py:492-563`](../backend/src/core/content_service.py#L492-L563)

**Request:**

```json
{ "name": "animals" }
```

**Response (200):**

```json
{ "restored": true, "name": "animals", "from_archive": "animals_1738456789" }
```

---

## 3. Category Icon

### 3.1 `PUT /api/v1/content/categories/{category_name}/icon`

| | |
|---|---|
| **Purpose** | Upload or replace the category icon image. |
| **When called** | User clicks the edit icon overlay on the category icon, selects a file in the upload modal. |
| **Backend status** | MISSING |

**Frontend code:**

- Edit icon button:
  [`CategoryDetail.vue:25-31`](../frontend/src/components/CategoryDetail.vue#L25-L31) — opens
  upload modal
- Upload modal:
  [`CategoryDetail.vue:140-164`](../frontend/src/components/CategoryDetail.vue#L140-L164) — file
  input with `accept="image/*"`
- File handler:
  [`CategoryDetail.vue:265-272`](../frontend/src/components/CategoryDetail.vue#L265-L272) —
  `onFileSelected()` emits `edit` event

**Request:**

```
Content-Type: multipart/form-data

image: <binary file>
```

**Expected response (200):**

```json
{ "category": "animals", "icon_url": "/content/animals/animals.png" }
```

---

## 4. Category Voices

### 4.1 `GET /api/v1/content/categories/{category_name}/voices`

| | |
|---|---|
| **Purpose** | List voice files for a category (one per supported language). |
| **When called** | When category detail loads; populates the voice cards grid. |
| **Backend status** | MISSING |

**Frontend code (currently uses mock):**

- Voice cards grid:
  [`CategoryDetail.vue:48-88`](../frontend/src/components/CategoryDetail.vue#L48-L88) — iterates
  `category.voices` from [`mockCategories`](../frontend/src/data/mockData.ts#L18-L23)
- Play button:
  [`CategoryDetail.vue:66-77`](../frontend/src/components/CategoryDetail.vue#L66-L77) — toggles
  audio playback using `voice.audioUrl`

**Expected response (200):**

```json
{
  "category": "animals",
  "voices": [
    {
      "id": "animals_en",
      "name": "Animals",
      "language": "en",
      "type": "tts",
      "audioUrl": "/content/animals/animals_en.mp3",
      "exists": true
    },
    {
      "id": "animals_es",
      "name": "Animales",
      "language": "es",
      "type": "tts",
      "audioUrl": "/content/animals/animals_es.mp3",
      "exists": true
    },
    {
      "id": "animals_fr",
      "name": "Animaux",
      "language": "fr",
      "type": "tts",
      "audioUrl": "/content/animals/animals_fr.mp3",
      "exists": true
    },
    {
      "id": "animals_bg",
      "name": "Животни",
      "language": "bg",
      "type": "tts",
      "audioUrl": "/content/animals/animals_bg.mp3",
      "exists": true
    }
  ]
}
```

---

### 4.2 `PUT /api/v1/content/categories/{category_name}/voices/{language}`

| | |
|---|---|
| **Purpose** | Upload or replace a category voice file for a specific language. |
| **When called** | User clicks "Edit Voice" on a voice card, selects an audio file in the upload modal. |
| **Backend status** | MISSING |

**Frontend code:**

- Edit voice button:
  [`CategoryDetail.vue:52-58`](../frontend/src/components/CategoryDetail.vue#L52-L58) — calls
  `openVoiceUpload(voice)`
- "Edit Voice" button:
  [`CategoryDetail.vue:79-85`](../frontend/src/components/CategoryDetail.vue#L79-L85)
- Upload voice modal:
  [`CategoryDetail.vue:166-191`](../frontend/src/components/CategoryDetail.vue#L166-L191) — file
  input with `accept="audio/*"`
- File handler:
  [`CategoryDetail.vue:242-250`](../frontend/src/components/CategoryDetail.vue#L242-L250) —
  `onVoiceFileSelected()` emits `edit-voice` event
- Parent handler: [`CategoriesView.vue:59-61`](../frontend/src/views/CategoriesView.vue#L59-L61) —
  `onEditVoice()` (currently `console.log`)

**Request:**

```
Content-Type: multipart/form-data

audio: <binary file>
```

**Expected response (200):**

```json
{
  "category": "animals",
  "language": "es",
  "audio_url": "/content/animals/animals_es.mp3"
}
```

---

### 4.3 `POST /api/v1/content/categories/{category_name}/voices/{language}/regenerate`

| | |
|---|---|
| **Purpose** | Regenerate a category voice file using the TTS pipeline (translate + synthesize). |
| **When called** | Could be triggered from a "Regenerate" button on a voice card (not yet in UI). |
| **Backend status** | MISSING (logic exists inside `create_category` and `rename_category`) |

**Backend reference (existing logic):**

- TTS generation helper: [`content_service.py:60-73`](../backend/src/core/content_service.py#L60-L73)
- Translation helper: [`content_service.py:34-57`](../backend/src/core/content_service.py#L34-L57)

**Expected response (200):**

```json
{
  "category": "animals",
  "language": "fr",
  "audio_url": "/content/animals/animals_fr.mp3"
}
```

---

## 5. Images (Objects)

### 5.1 `POST /api/v1/content/objects`

| | |
|---|---|
| **Purpose** | Create a new image/object in a category (saves image, auto-translates, generates per-language voice files). |
| **When called** | User clicks "Add New Image" button on the category view. |
| **Backend status** | EXISTS |

**Frontend code:**

- Add button:
  [`CategoriesView.vue:16-18`](../frontend/src/views/CategoriesView.vue#L16-L18)
- Handler: [`CategoriesView.vue:65-67`](../frontend/src/views/CategoriesView.vue#L65-L67) —
  `onAddImage()` (currently `console.log`)

**Backend code:**

- Route: [`routes_content.py:113-142`](../backend/src/api/v1/routes_content.py#L113-L142)
- Service: [`content_service.py:256-337`](../backend/src/core/content_service.py#L256-L337)

**Request:**

```
Content-Type: multipart/form-data

image:    <binary file (.png/.jpg)>
category: nature_scenes
object:   Mountain Peak
```

**Response (201):**

```json
{
  "category": "nature_scenes",
  "object": "Mountain Peak",
  "folder": "mountain_peak",
  "image_id": "mountain_peak"
}
```

---

### 5.2 `GET /api/v1/content/categories/{category_name}/objects/{object_name}`

| | |
|---|---|
| **Purpose** | Get full details for one image/object (files, thumbnail, per-language voices). |
| **When called** | When user navigates to the image detail page via sidebar or image grid click. |
| **Backend status** | MISSING |

**Frontend code (currently uses mock):**

- Route: [`router/index.ts:31-34`](../frontend/src/router/index.ts#L31-L34) —
  `/categories/:categoryId/images/:imageId`
- Image lookup:
  [`ImageDetailView.vue:139-147`](../frontend/src/views/ImageDetailView.vue#L139-L147) — finds
  image from `mockCategories`
- Image grid (navigates here):
  [`ImageGrid.vue:38-39`](../frontend/src/components/ImageGrid.vue#L38-L39) —
  `navigateToDetail()`
- Thumbnail display:
  [`ImageGrid.vue:9-14`](../frontend/src/components/ImageGrid.vue#L9-L14)

**Expected response (200):**

```json
{
  "category": "nature_scenes",
  "name": "Mountain Peak",
  "folder": "mountain_peak",
  "image_id": "mountain_peak",
  "files": [
    "/content/nature_scenes/mountain_peak/01.png",
    "/content/nature_scenes/mountain_peak/02.png"
  ],
  "thumbnail_url": "/content/nature_scenes/mountain_peak/01.png",
  "voices": [
    {
      "id": "mountain_peak_en",
      "name": "Mountain Peak",
      "language": "en",
      "type": "tts",
      "audioUrl": "/content/nature_scenes/mountain_peak/mountain_peak_en_01.mp3",
      "exists": true
    },
    {
      "id": "mountain_peak_es",
      "name": "Pico de Montaña",
      "language": "es",
      "type": "tts",
      "audioUrl": "/content/nature_scenes/mountain_peak/mountain_peak_es_01.mp3",
      "exists": true
    },
    {
      "id": "mountain_peak_fr",
      "name": "Sommet de Montagne",
      "language": "fr",
      "type": "tts",
      "audioUrl": "/content/nature_scenes/mountain_peak/mountain_peak_fr_01.mp3",
      "exists": true
    },
    {
      "id": "mountain_peak_bg",
      "name": "Планински Връх",
      "language": "bg",
      "type": "tts",
      "audioUrl": "/content/nature_scenes/mountain_peak/mountain_peak_bg_01.mp3",
      "exists": true
    }
  ]
}
```

---

### 5.3 `PATCH /api/v1/content/categories/{category_name}/objects/{object_name}`

| | |
|---|---|
| **Purpose** | Rename an image/object (update folder, CSVs, regenerate voices). |
| **When called** | User edits the image name in the text input on the image detail page. |
| **Backend status** | MISSING |

**Frontend code:**

- Name input:
  [`ImageDetailView.vue:25-30`](../frontend/src/views/ImageDetailView.vue#L25-L30) — binds to
  `image.name`
- Handler:
  [`ImageDetailView.vue:155-158`](../frontend/src/views/ImageDetailView.vue#L155-L158) —
  `onNameChange()` (currently `console.log`)

**Request:**

```json
{ "name": "Alpine Peak" }
```

**Expected response (200):**

```json
{
  "category": "nature_scenes",
  "old_name": "mountain_peak",
  "new_name": "alpine_peak",
  "message": "Object renamed."
}
```

---

### 5.4 `DELETE /api/v1/content/categories/{category_name}/objects/{object_name}`

| | |
|---|---|
| **Purpose** | Delete an image/object and all its files, voices, and CSV entries. |
| **When called** | Not yet wired in UI — expected from a delete button on the image detail page. |
| **Backend status** | MISSING |

**Expected response (200):**

```json
{ "category": "nature_scenes", "name": "mountain_peak", "deleted": true }
```

---

## 6. Image Files

### 6.1 `POST /api/v1/content/categories/{category_name}/objects/{object_name}/files`

| | |
|---|---|
| **Purpose** | Add an additional image file to an existing object. |
| **When called** | User clicks "Add File" on the image detail page and selects an image. |
| **Backend status** | MISSING |

**Frontend code:**

- Add file button:
  [`ImageDetailView.vue:40-42`](../frontend/src/views/ImageDetailView.vue#L40-L42)
- Handler:
  [`ImageDetailView.vue:159-161`](../frontend/src/views/ImageDetailView.vue#L159-L161) —
  `onAddFile()` (currently `console.log`)

**Request:**

```
Content-Type: multipart/form-data

image: <binary file>
```

**Expected response (201):**

```json
{
  "category": "nature_scenes",
  "object": "mountain_peak",
  "file": "02.png",
  "file_url": "/content/nature_scenes/mountain_peak/02.png"
}
```

---

### 6.2 `PUT /api/v1/content/categories/{category_name}/objects/{object_name}/files/{index}`

| | |
|---|---|
| **Purpose** | Replace an image file at a specific index position. |
| **When called** | User clicks the pencil overlay on an image file card in the detail view. |
| **Backend status** | MISSING |

**Frontend code:**

- Replace button:
  [`ImageDetailView.vue:58-63`](../frontend/src/views/ImageDetailView.vue#L58-L63) — pencil icon
  overlay
- Handler:
  [`ImageDetailView.vue:162-164`](../frontend/src/views/ImageDetailView.vue#L162-L164) —
  `onReplaceFile(index)` (currently `console.log`)

**Request:**

```
Content-Type: multipart/form-data

image: <binary file>
```

**Expected response (200):**

```json
{
  "category": "nature_scenes",
  "object": "mountain_peak",
  "file": "01.png",
  "file_url": "/content/nature_scenes/mountain_peak/01.png"
}
```

---

### 6.3 `DELETE /api/v1/content/categories/{category_name}/objects/{object_name}/files/{index}`

| | |
|---|---|
| **Purpose** | Delete an image file at a specific index (at least one file must remain). |
| **When called** | User clicks the delete overlay on an image file card. Disabled when only one file remains. |
| **Backend status** | MISSING |

**Frontend code:**

- Delete button:
  [`ImageDetailView.vue:64-72`](../frontend/src/views/ImageDetailView.vue#L64-L72) — delete icon
  overlay, disabled when `image.files.length <= 1`
- Handler:
  [`ImageDetailView.vue:165-167`](../frontend/src/views/ImageDetailView.vue#L165-L167) —
  `onDeleteFile(index)` (currently `console.log`)

**Expected response (200):**

```json
{ "category": "nature_scenes", "object": "mountain_peak", "deleted_file": "02.png" }
```

---

## 7. Image/Object Voices

### 7.1 `PUT /api/v1/content/categories/{category_name}/objects/{object_name}/voices/{language}`

| | |
|---|---|
| **Purpose** | Upload or replace a voice file for one language on an image/object. |
| **When called** | User clicks "Replace" in the voices table on the image detail page. |
| **Backend status** | MISSING |

**Frontend code:**

- Replace button per language row:
  [`ImageDetailView.vue:113-115`](../frontend/src/views/ImageDetailView.vue#L113-L115)
- Handler:
  [`ImageDetailView.vue:168-170`](../frontend/src/views/ImageDetailView.vue#L168-L170) —
  `onUploadVoice(language)` (currently `console.log`)

**Request:**

```
Content-Type: multipart/form-data

audio: <binary file>
```

**Expected response (200):**

```json
{
  "category": "nature_scenes",
  "object": "mountain_peak",
  "language": "es",
  "audio_url": "/content/nature_scenes/mountain_peak/mountain_peak_es_01.mp3"
}
```

---

### 7.2 `POST /api/v1/content/categories/{cat}/objects/{obj}/voices/{language}/regenerate`

| | |
|---|---|
| **Purpose** | Regenerate a voice file for one language using the TTS pipeline. |
| **When called** | Could be triggered from a "Regenerate" action in the voices table (not yet in UI). |
| **Backend status** | MISSING (logic exists in `create_object`) |

**Backend reference:**

- Object TTS loop: [`content_service.py:316-330`](../backend/src/core/content_service.py#L316-L330)

**Expected response (200):**

```json
{
  "category": "nature_scenes",
  "object": "mountain_peak",
  "language": "fr",
  "audio_url": "/content/nature_scenes/mountain_peak/mountain_peak_fr_01.mp3"
}
```

---

## 8. Static Content Serving

### 8.1 `GET /content/{path}`

| | |
|---|---|
| **Purpose** | Serve all content files (images, audio, icons) from the content directory. |
| **When called** | Every time the browser renders an `<img>` or plays audio with a `/content/...` URL. |
| **Backend status** | MISSING |

**Frontend code that depends on this:**

- Category icon: [`CategoryDetail.vue:19-23`](../frontend/src/components/CategoryDetail.vue#L19-L23)
  — `<img :src="category.iconUrl">`
- Image thumbnails: [`ImageGrid.vue:9-14`](../frontend/src/components/ImageGrid.vue#L9-L14) —
  `<img :src="image.thumbnailUrl">`
- Image files: [`ImageDetailView.vue:51-56`](../frontend/src/views/ImageDetailView.vue#L51-L56) —
  `<img :src="file">`
- Voice audio playback:
  [`CategoryDetail.vue:66-77`](../frontend/src/components/CategoryDetail.vue#L66-L77)

**Implementation suggestion:**

```python
from fastapi.staticfiles import StaticFiles
app.mount("/content", StaticFiles(directory=settings.CONTENT_DIR), name="content")
```

---

## Summary

| # | Method | Path | Purpose | Backend |
|---|--------|------|---------|---------|
| 1.1 | POST | `/api/v1/login` | Authenticate user | EXISTS |
| 1.2 | GET | `/api/v1/me` | Current user profile | MISSING |
| 2.1 | GET | `/api/v1/content/categories` | List all categories | EXISTS* |
| 2.2 | POST | `/api/v1/content/categories` | Create category | EXISTS |
| 2.3 | GET | `/api/v1/content/categories/{name}` | Category details | EXISTS* |
| 2.4 | PATCH | `/api/v1/content/categories/{name}` | Rename category | EXISTS |
| 2.5 | DELETE | `/api/v1/content/categories/{name}` | Delete/archive category | EXISTS |
| 2.6 | POST | `/api/v1/content/categories/restore` | Restore archived category | EXISTS |
| 3.1 | PUT | `/api/v1/content/categories/{name}/icon` | Upload category icon | MISSING |
| 4.1 | GET | `/api/v1/content/categories/{name}/voices` | List category voices | MISSING |
| 4.2 | PUT | `/api/v1/content/categories/{name}/voices/{lang}` | Upload category voice | MISSING |
| 4.3 | POST | `.../voices/{lang}/regenerate` | Regenerate category voice | MISSING |
| 5.1 | POST | `/api/v1/content/objects` | Create image/object | EXISTS |
| 5.2 | GET | `.../categories/{name}/objects/{obj}` | Object details | MISSING |
| 5.3 | PATCH | `.../categories/{name}/objects/{obj}` | Rename object | MISSING |
| 5.4 | DELETE | `.../categories/{name}/objects/{obj}` | Delete object | MISSING |
| 6.1 | POST | `.../objects/{obj}/files` | Add image file | MISSING |
| 6.2 | PUT | `.../objects/{obj}/files/{idx}` | Replace image file | MISSING |
| 6.3 | DELETE | `.../objects/{obj}/files/{idx}` | Delete image file | MISSING |
| 7.1 | PUT | `.../objects/{obj}/voices/{lang}` | Upload object voice | MISSING |
| 7.2 | POST | `.../objects/{obj}/voices/{lang}/regenerate` | Regenerate object voice | MISSING |
| 8.1 | GET | `/content/{path}` | Serve static content files | MISSING |

\* Response shape needs enrichment to match frontend types.

**Totals: 22 endpoints required, 8 exist in backend, 14 missing.**
