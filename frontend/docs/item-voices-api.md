# Item Voices: frontend integration and missing API support

The language accordion/card layout and mobile dialogs are implemented in the frontend. No backend code or generated API schemas were changed. Existing upload/replace and audio URLs remain usable. New candidate operations are enabled only when the voices response advertises the corresponding capability; the frontend never substitutes regeneration (which overwrites audio) for creating a new take.

## Existing API behavior

Base path: `/api/v1/content/categories/{category_id}/items/{item_id}/voices`.

| Endpoint | Current behavior |
| --- | --- |
| `GET /voices` | Returns one entry per supported language, including empty placeholders. The filesystem provider returns the first matching MP3, not every candidate. |
| `POST /voices` | Multipart `language` + `audio`. Adds the first recording for a language. Returns 409 if one already exists. |
| `PUT /voices/{voice_id}` | Multipart `audio`. Replaces an existing recording. Returns 404 for an empty placeholder. |
| `DELETE /voices/{voice_id}` | Deletes/resets a recording. Existing summary-card controls retain this action. |
| `POST /voices/{voice_id}/regenerate` | Overwrites the current file using the active configuration. This is retained in the existing Item Voices summary; it is not used by the new Generate dialog. |

Legacy uploads are restricted to MP3 in the new section because the current implementation writes bytes to an `.mp3` filename without transcoding or preserving the supplied extension. The upload dialog supports WAV when a future API advertises it. Replacement of the legacy recording changes the item immediately; the dialog explains this before submission.

## Missing endpoints (proposed contract)

The following paths are relative to the base path above. These contracts are handwritten in `src/api/itemVoicesClient.ts` / `src/types/itemVoices.ts`, and simulated only in the frontend MSW preview.

| Method and suffix | Required behavior |
| --- | --- |
| `GET /presets?language=en` | Return usable per-language base voices: `id`, `provider`, `name`, `model`, `language`, `speed`, and optional `stability`/`similarity` defaults. |
| `POST /generate` | Create and persist a **new** candidate for the item. Body: `language`, `text`, `preset_id`, `speed`, optional `stability`/`similarity`, and `optimize`. Return the created voice. Keep the existing selection. |
| `POST /{voice_id}/optimize` | Persist an optimized version while retaining the original. Return the voice with `optimized_file_uri`. Do not select a different voice as a side effect. |
| `PUT /{voice_id}/selection` | Body: `{ "version": "original" \| "optimized" }`. Atomically select this voice/version for its language. Return the updated voice and preserve selections for other languages. Reject missing audio/versions. |

The existing internal generator endpoints are not substitutes: `/internal/generators/` omits the language-specific base voices; `/internal/generators/generate-word` uses the highest-priority configuration; `/{provider}/generate` accepts pipeline input and returns filesystem paths, not a saved item candidate with per-request voice settings. Similarly `/internal/optimizers/{optimizer}/optimize` requires server filesystem paths and does not attach a version to an item. The browser should not infer or send server filesystem paths from public audio URLs.

## Required extensions to existing endpoints

- Extend `GET /voices` to enumerate every candidate with stable unique IDs. Add `optimized_file_uri`, `is_selected`, `selected_version`, optional `display_name`, and `creation_method` (`generated`, `uploaded`, or `unknown`). Retain `provider` and `parameters`, including actual generation metadata and creation time. Return real audio URLs; the UI does not invent optimized filenames.
- Extend `POST /voices` to append additional candidates for a language without overwriting the current recording. Preserve the actual format or transcode uploads. Keep `PUT /voices/{voice_id}` scoped to replacing that particular candidate, invalidate an obsolete optimized version, and return refreshed metadata.
- Return a `capabilities` object on `GET /voices`. Only set a flag to true when the corresponding operation works for the current user/item. The frontend defaults missing flags to false and missing upload formats to MP3.

Example upgraded list response:

```json
{
  "item_id": "I_Bananas",
  "name": { "en": "bananas", "es": "plátano", "fr": "banane", "bg": "банани" },
  "capabilities": {
    "multiple_voices": true,
    "generate": true,
    "optimize": true,
    "select": true,
    "presets": true,
    "upload_formats": ["mp3", "wav"]
  },
  "voices": [{
    "id": "bananas-en-take-1",
    "language": "en",
    "type": "tts",
    "display_name": "Milena",
    "creation_method": "generated",
    "provider": "elevenlabs",
    "file_uri": "/content/fruits/bananas/take-1.mp3",
    "optimized_file_uri": "/content/fruits/bananas/take-1-optimized.mp3",
    "is_selected": true,
    "selected_version": "optimized",
    "parameters": {
      "actor": "Milena",
      "model": "eleven_multilingual_v2",
      "speed": 1.0,
      "voice_settings": { "stability": 0.5, "similarity_boost": 0.75 },
      "date": "2026-09-28T08:00:00Z"
    }
  }]
}
```

For each language, at most one candidate is selected. No candidate may be selected when its chosen audio version is unavailable. Empty language groups remain visible so users can add their first recording. A legacy single recording is displayed as selected because it is already the audio used for that item; once `multiple_voices` is enabled the API must explicitly identify the selection.

## Preview and verification

Run `npm run test:voices` (Node 22.6+; the project uses Node 24) for the selection, version-URL, upload-format, and preview-URL regression tests. Run `npm run build` for TypeScript and the production bundle.

Run `VITE_MOCK_DATA=true npm run dev` and open `/categories/C_Fruits/items/I_Bananas`. The preview seeds generated/manual alternatives, supports selection and upload/replacement, and simulates generation/optimization using sample audio. The visible preview notice makes this explicit. All preview state is in browser memory and resets on reload; no files in the content directory are modified.

The existing page summary and image section remain in place. The new section is a three-column card grid on wide desktop, two columns on narrower layouts, and one column on mobile. Generation and upload dialogs become full-screen mobile forms with reachable actions, native modal focus containment, and file-picker alternatives to drag-and-drop. New controls are translated into English, Spanish, French, and Bulgarian.
