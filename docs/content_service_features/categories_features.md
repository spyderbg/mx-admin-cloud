# Categories section: supported features

Inventory of every operation available in the **Categories** section (categories, their items, and the images and voices of both), split by where the operation can be used.

- **CLI** means a `kiddo-cli content ...` command. It can be called from a shell or a script.
- **Web only** means the operation exists only in the web UI. The backend endpoint behind it is listed, because it can still be scripted over plain HTTP (for example `curl` with a bearer token). There is no CLI command for it.

Each feature has a checkbox (`- [ ]`). Tick it by editing `[ ]` to `[x]`, or click it in a Markdown preview that supports task lists.

Sources: backend routes `backend/src/services/content/routes/{categories,items}.py`, CLI `../../cli/src/kiddo_cli/commands/content_commands.py`, frontend `frontend/src/api/contentClient.ts` and the views and components behind it (`CategoriesView`, `CategoryDetail`, `DashboardView`, `ItemDetailsView`, `AddItemModal`, `ItemVoicesSection`).

All endpoints are under `/api/v1/content` and require authentication. Mutations require the `admin` role.

---

## 1. Supported by CLI

Run as `kiddo-cli content <command>`. Every command accepts `--url` and `--token`. Without them the CLI authenticates through the Unix socket or the `KIDDO_*` environment variables.

- [ ] **List categories**
  - Command: `categories`
  - Endpoint: `GET /categories`
  - Notes: Prints JSON. `--include-archived` adds archived categories. `--archived-only` lists only archived ones.
- [ ] **Show category details**
  - Command: `category-details <category>`
  - Endpoint: `GET /categories/{category_id}`
  - Notes: Prints JSON with the category's names, description, icon, voices and items.
- [ ] **Create a category**
  - Command: `create-category --name <name>`
  - Endpoint: `POST /categories`
  - Notes: Creates the folder, voices and icon server-side. Timeout 120 s.
- [ ] **Change a category's id**
  - Command: `rename-category <id or name> --to <new id>`
  - Endpoint: `GET` then `PATCH /categories/{category_id}`
  - Notes: Accepts the id or English name. Resends the current names and description unchanged, as the web UI does. Editing the localized names is web only.
- [ ] **Archive / remove a category**
  - Command: `remove-category <category>`
  - Endpoint: `DELETE /categories/{category_id}`
  - Notes: Archives when `ARCHIVE_DIR` is set on the server. Otherwise it deletes permanently.
- [ ] **Restore a category from the archive**
  - Command: `restore-category <name or archive folder>`
  - Endpoint: `POST /categories/restore`
  - Notes: Accepts the base name (newest snapshot) or the full folder, for example `foods_1738456789`.
- [ ] **Add a new item to a category**
  - Command: `create-item --category <id or name> --item <english word> [--image <file>]`
  - Endpoint: `POST /categories/{category_id}/items`
  - Notes: Resolves the category by id (`C_Foods`) or by English name (`foods`). The image is optional. The server translates the word into every language and generates voices if a TTS provider is active. Failed voice generation is reported in `voice_warnings` and does not block creation.
- [ ] **Add an image to an item (at creation only)**
  - Command: `create-item ... --image <file>`
  - Endpoint: same call
  - Notes: Image upload is only available as part of item creation. There is no standalone "add image to existing item" command.

### Scripts

- [ ] **List categories (`include_archived=false`)**
  - Script: `../../scripts/list_categories.sh`. It calls `GET /categories` with curl and `$KIDDO_WORDS_TOKEN`.

### Known issues in the CLI commands (found while reading the code)

- **`remove-category` and `category-details` put the argument straight into the URL path.** The backend expects a `category_id` such as `C_Foods`. Only `create-item` resolves a plain name such as `foods` to an id.
- **`create-item --image` sends the raw file.** The web UI downsizes images in the browser (`resizeImageToMaxDimension`) before upload. The CLI does not.

---

## 2. Web only

Operations available from the web UI only. Each one has a backend endpoint, but no CLI command.

### 2.1 Category list and archive (Dashboard)

- [ ] **Browse category cards with icons**
  - Where in the UI: Dashboard and sidebar
  - Endpoint: `GET /categories`
- [ ] **Show the archived-categories section**
  - Where in the UI: Dashboard
  - Endpoint: `GET /categories?include_archived=true`
- [ ] **Restore a specific archive snapshot**
  - Where in the UI: Dashboard, archived section, **Restore**
  - Endpoint: `POST /categories/restore` with the full archive folder name
- [ ] **Permanently delete an archived category**
  - Where in the UI: Dashboard, archived section, **Delete**. The user must type the category id to confirm.
  - Endpoint: `DELETE /categories/archive/{archive_name}`

The CLI can restore by base name, but it cannot permanently delete an archive.

### 2.2 Category editing (category page)

- [ ] **Change the category id**
  - Where in the UI: Pencil next to the id
  - Endpoint: `PATCH /categories/{category_id}` with `new_category_id`
- [ ] **Edit the localized category name per language**
  - Where in the UI: Voice cards (rename-voice flow)
  - Endpoint: `PATCH /categories/{category_id}` with `category_name` and `description`
- [ ] **Upload or replace the category icon**
  - Where in the UI: Pencil on the icon. The image is resized client-side.
  - Endpoint: `POST /categories/{category_id}/icon`
- [ ] **Delete the category icon**
  - Where in the UI: Trash on the icon
  - Endpoint: `DELETE /categories/{category_id}/icon`
- [ ] **Preview the icon full size**
  - Where in the UI: Click the icon
  - Endpoint: none (client-side)
- [ ] **Archive a category with confirmation**
  - Where in the UI: **Archive category** button. By default the user must type the id. `VITE_ARCHIVE_CONFIRMATION_MODE=simple` switches to a Yes/No dialog.
  - Endpoint: `DELETE /categories/{category_id}`
- [ ] **Upload or replace a category voice (audio file per language)**
  - Where in the UI: Category voice cards
  - Endpoint: `POST /categories/{category_id}/voices` to add, `PUT .../voices/{voice_id}` to replace
- [ ] **Delete a category voice**
  - Where in the UI: Category voice cards
  - Endpoint: `DELETE /categories/{category_id}/voices/{voice_id}`
- [ ] **Regenerate a category voice**
  - Where in the UI: Category voice cards, **Regenerate voice**
  - Endpoint: `POST /categories/{category_id}/voices/{voice_id}/regenerate`
- [ ] **Play a category voice in the browser**
  - Where in the UI: Category voice cards
  - Endpoint: none (client-side)

### 2.3 Category image library (backend and generated client only, no UI)

These endpoints exist and the generated TypeScript client wraps them, but **no view calls them**. They are also not in the CLI, so today they can only be used over raw HTTP.

- [ ] **List images in a category**
  - Endpoint: `GET /categories/{category_id}/images`
- [ ] **Add an image to a category**
  - Endpoint: `POST /categories/{category_id}/images`
- [ ] **Replace a category image**
  - Endpoint: `PUT /categories/{category_id}/images/{image_id}`
- [ ] **Delete a category image**
  - Endpoint: `DELETE /categories/{category_id}/images/{image_id}`
- [ ] **List voices for a category**
  - Endpoint: `GET /categories/{category_id}/voices` (the UI gets voices through category details instead)

### 2.4 Items

- [ ] **Browse items as a grid**
  - Where in the UI: Category page. A slider sets 1 to 7 columns, and the choice is persisted.
  - Endpoint: `GET /categories/{category_id}` or `GET .../items`
- [ ] **Open the item detail page**
  - Where in the UI: Click an item
  - Endpoint: `GET .../items/{item_id}`
- [ ] **Create an item (with the same server-side translation and voices as the CLI)**
  - Where in the UI: **Add new item** modal. It accepts PNG or JPEG, resizes the image, and shows a preview. On success it opens the item. If there are voice warnings it shows a warning toast and stays on the category page.
  - Endpoint: `POST /categories/{category_id}/items`
- [ ] **Change an item id**
  - Where in the UI: Pencil next to the id
  - Endpoint: `PUT .../items/{item_id}` with `new_item_id`
- [ ] **Edit an item's localized names**
  - Where in the UI: Voice cards (rename flow)
  - Endpoint: `PUT .../items/{item_id}` with `item_name`
- [ ] **Delete an item**
  - Where in the UI: **Delete** button. The user must type the item id to confirm.
  - Endpoint: `DELETE .../items/{item_id}`

The CLI can only create items. It cannot list, get, rename or delete them.

### 2.5 Item images

- [ ] **List image variants**
  - Where in the UI: Image gallery
  - Endpoint: `GET .../items/{item_id}/images`
- [ ] **Add an image to an existing item**
  - Where in the UI: **Add image**, or the add tile with drop support
  - Endpoint: `POST .../items/{item_id}/images`
- [ ] **Replace an image variant**
  - Where in the UI: Pencil on the image card
  - Endpoint: `PUT .../items/{item_id}/images/{image_id}`
- [ ] **Delete an image variant**
  - Where in the UI: Trash on the image card
  - Endpoint: `DELETE .../items/{item_id}/images/{image_id}`
- [ ] **Choose which image is the main one**
  - Where in the UI: **Select** on an image card
  - Endpoint: `PUT .../items/{item_id}/images/{image_id}/main`
- [ ] **Preview an image full size**
  - Where in the UI: Click the image
  - Endpoint: none (client-side)

### 2.6 Item voices

- [ ] **List voices, grouped by language, showing which one is in use**
  - Where in the UI: Item voices section
  - Endpoint: `GET .../items/{item_id}/voices`
- [ ] **Add a voice by uploading an audio file**
  - Where in the UI: **Add** dialog. It validates the file and picks a language.
  - Endpoint: `POST .../items/{item_id}/voices`
- [ ] **Replace a voice's audio**
  - Where in the UI: **Replace** dialog
  - Endpoint: `PUT .../items/{item_id}/voices/{voice_id}`
- [ ] **Delete a voice**
  - Where in the UI: Delete dialog
  - Endpoint: `DELETE .../items/{item_id}/voices/{voice_id}`
- [ ] **Generate a new voice take with TTS**
  - Where in the UI: **Generate** dialog. The user picks text, language, TTS configuration, speed, stability and similarity, and can turn on auto-optimize. The new take is added and the current voice stays selected.
  - Endpoint: `POST .../items/{item_id}/voices/generate`
- [ ] **Optimize a voice**
  - Where in the UI: Voice card, **Optimize**
  - Endpoint: `POST .../items/{item_id}/voices/{voice_id}/optimize`
- [ ] **Select which voice, and original or optimized version, is in use for a language**
  - Where in the UI: Voice card, **Select**
  - Endpoint: `PUT .../items/{item_id}/voices/{voice_id}/main`
- [ ] **Regenerate one voice and select it**
  - Where in the UI: Voice card, **Regenerate voice**. It generates a new take with the default TTS configuration and selects it.
  - Endpoint: `POST .../voices/generate`, then `PUT .../voices/{voice_id}/main`
- [ ] **Regenerate all voices of an item**
  - Where in the UI: **Regenerate all voices**, with confirmation. It reports partial failures.
  - Endpoint: same two calls, repeated per voice
- [ ] **Play a voice in the browser**
  - Where in the UI: Voice cards
  - Endpoint: none (client-side)

The backend also exposes `POST .../items/{item_id}/voices/{voice_id}/regenerate`, which is declared in the generated client but not used by the item page.

---

## 3. Not implemented anywhere (UI stubs)

These controls are visible in the web UI but only show a "Backend task pending" notice. They are neither web nor CLI features yet.

| Control | Location |
|---|---|
| Regenerate the category image from saved metadata | Category page, sparkle button on the icon |
| Release item | Item page, **Release** button |
| Release all item images | Item page, image actions |

The voice-presets endpoint (`GET .../voices/presets`) used by the voice dialog is served only by frontend mock fixtures. The backend does not implement it.

---

## 4. CLI coverage at a glance

| Area | Operations in the backend | In the CLI |
|---|---|---|
| Categories | 6 (list, create, details, update, remove, restore), plus archive delete | 6 (rename covers the id only). Archive delete is missing. |
| Category icon | 2 | 0 |
| Category images | 4 | 0 |
| Category voices | 5 (list, add, replace, delete, regenerate) | 0 |
| Items | 5 (list, create, get, update, delete) | 1 (create) |
| Item images | 5 | 0 (image only at item creation) |
| Item voices | 8 | 0 |
