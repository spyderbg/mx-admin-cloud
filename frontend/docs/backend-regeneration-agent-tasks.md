# Backend Regeneration Agent Tasks

The frontend now exposes regenerate buttons for category images, category voices, item images, and item voices. The buttons intentionally show a pending-backend toast until the API contract below exists and the TypeScript client is regenerated.

## Goal

Add backend endpoints that regenerate existing content assets using the saved category/item metadata, persist the generated file through the existing content storage layout, and return the same response shapes used by current image and voice upload/update endpoints.

## Required API Endpoints

Implement these operations in the FastAPI backend:

- `POST /content/categories/{category_id}/images/regenerate`
  - Regenerates or replaces the category thumbnail/icon image.
  - Returns `ImageResponse`.

- `POST /content/categories/{category_id}/voices/{voice_id}/regenerate`
  - Regenerates audio for one category voice language.
  - Returns `VoiceResponse`.

- `POST /content/categories/{category_id}/items/{item_id}/images/{image_id}/regenerate`
  - Regenerates or replaces a specific item image variant.
  - Returns `ImageResponse`.

- `POST /content/categories/{category_id}/items/{item_id}/voices/{voice_id}/regenerate`
  - Regenerates audio for one item voice language.
  - Returns `VoiceResponse`.

## Behavior Requirements

- Use the current category/item localized names and descriptions as the source prompt context.
- For image regeneration, overwrite the targeted asset or create a new canonical asset when none exists for the category thumbnail.
- For voice regeneration, use the target voice language, display word, and provider defaults already configured for the TTS pipeline.
- Return `404` when the category, item, image, or voice target cannot be found.
- Return `409` or `422` when required source metadata is missing.
- Keep file URIs stable when replacing an existing asset so the frontend cache-busting behavior continues to work.
- Invalidate or update any backend-side content indexes/caches used by list/detail endpoints.

## Frontend Follow-Up After Backend Merge

- Run the project API sync command from the repository root so `src/api/contentClient.ts` includes the new operations.
- Replace the pending toast handlers in `src/components/CategoryDetail.vue` and `src/views/ItemDetailsView.vue` with real API calls.
- After each successful regeneration, invalidate the relevant frontend cache and refresh the detail data.
- Preserve the existing success/error toast copy pattern and cache-busting version increments.
