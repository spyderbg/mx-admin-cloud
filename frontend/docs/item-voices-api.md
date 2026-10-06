# Item Voices: frontend integration and remaining API support

The language accordion/card layout and mobile dialogs are implemented in the frontend. Selection and generation use the generated `itemsVoicesSetMain` and `itemsVoicesGenerate` clients.

## Implemented voice selection

Base path: `/api/v1/content/categories/{category_id}/items/{item_id}/voices`.

“Use for item” sends `PUT /{voice_id}/main` with the selected recording’s language:

```json
{ "language": "en" }
```

The request uses the variant’s actual ID, including when selecting a separately listed optimized variant. It does not send a `version` property or call `/selection`. Success refreshes both the Voices section and Item Voices summary, including their audio URLs. A failed request displays an error without changing the current selection.

`GET /voices` and the selection response return `ItemVoicesResponse`:

```json
{
  "item_id": "I_Bananas",
  "name": { "en": "bananas", "es": "plátano", "fr": "banane", "bg": "банани" },
  "voices": [
    { "id": "bananas-v-en", "language": "en", "type": "tts", "file_uri": "/content/fruits/bananas/bananas_en.mp3" }
  ],
  "variants": [
    { "id": "bananas_en_01", "language": "en", "type": "tts", "file_uri": "/content/fruits/bananas/bananas_en_01.mp3" },
    { "id": "bananas_en_02", "language": "en", "type": "tts", "file_uri": "/content/fruits/bananas/bananas_en_02.mp3" }
  ],
  "main_voice_ids": { "en": "bananas_en_02", "es": null, "fr": null, "bg": null }
}
```

The frontend renders `variants`, marks selection using `main_voice_ids`, and avoids duplicating the current main recording. A standalone main file with no matching variant remains visible and selected. Empty language groups remain visible. The presence of `variants` and `main_voice_ids` enables selection without requiring the proposed `capabilities` extension; an explicit `capabilities.select: false` disables it.

## Other existing endpoints

| Suffix | Behavior |
| --- | --- |
| `POST /` | Multipart `language` + `audio`. Adds the first recording for a language; currently returns 409 if one already exists. |
| `PUT /{voice_id}` | Multipart `audio`. Replaces a recording; empty placeholders return 404. |
| `DELETE /{voice_id}` | Deletes/resets a recording. Existing summary controls retain this action. |
| `POST /{voice_id}/regenerate` | Adds a new take: synthesizes the item's current text (using the active TTS configuration) into a new numbered variant and returns it. Nothing is overwritten. The current main voice stays as it is, and is first kept as a variant if it is not one already, so it can be chosen again. `voice_id` may be a per-language id or a variant id. Only a language with no recording yet gets the take as its main voice. The result is not in use until it is selected with `PUT /{voice_id}/main`. It takes only the item's text: per-request text, configuration and settings use `POST /generate` below. |
| `POST /{voice_id}/optimize` | Now implemented in the backend: returns the updated voice lists, `source_voice_id`, `optimized_voice_ids`, and warnings. Adapting the optimization UI from its inline preview contract to this response remains separate work. |

Uploads remain restricted to MP3 unless the API advertises additional formats, because the existing implementation writes bytes to an `.mp3` filename. Listing multiple variants does not imply that the upload endpoint can append recordings.

## Generating a voice

The Generate Voice dialog loads saved text-to-speech configurations through `GET /internal/configurations/text_to_speech` and their detail endpoints, filters them for the selected language, and uses their provider, model and settings defaults. Selecting a configuration does not change its saved settings or enabled state, and a disabled configuration can still be picked. The speed slider is limited to what the configuration's provider accepts (ElevenLabs and the static provider 0.7–1.2, OpenAI 0.25–4, others 0.5–2).

`POST /generate` (status 201) adds a new take:

```json
{ "language": "en", "text": "bananas", "configuration_name": "elevenlabs", "speed": 0.9, "stability": 0.5, "similarity": 0.75, "optimize": true }
```

- The text is synthesized with the named configuration, enabled or not. `speed`, `stability` and `similarity` are optional and override the configuration's own settings for this request only; the saved configuration is never changed.
- The take is added as a new numbered variant. The main voice is not changed (it is first kept as a variant if it is not one already), so the take is not selected until `PUT /{voice_id}/main`. Only a language with no recording yet gets the take as its main voice, the same rule as `regenerate`.
- With `optimize`, the active voice optimizers run on the new take afterwards, following the voice optimization strategy. Optimizer failures are returned in `warnings` and the take is kept; the dialog shows them in an error toast.
- The response is `ItemVoicesResponse` plus `voice_id` (the new take), `optimized_voice_ids` and `warnings`. The generation parameters, the synthesized text (`_text`) and `configuration_name` are recorded in the take's metadata, which the voice card's generation popup shows.

## Remaining extensions

The following paths are relative to the base path above.

| Endpoint | Required behavior |
| --- | --- |
| Extend `POST /voices` | Append additional uploads for a language without overwriting the current recording; preserve the format or transcode it. |
| `capabilities` on `GET /voices` | **Implemented** (only on the list response). Advertises `select: true`, `optimize` (true while a voice optimizer is active), `generate` (true while at least one text-to-speech configuration exists), `multiple_voices: false`, `presets: false` and `upload_formats: ["mp3"]`. Flip `multiple_voices` when the upload extension above exists. The configuration selector uses the existing configuration API and does not require the presets capability. |

Existing internal generator endpoints are not substitutes for saved per-item candidates with per-request settings. The browser should not infer server filesystem paths from public audio URLs.

The mock preview retains inline Original/Optimized tabs. These use an explicit `optimized_voice_id` alongside `optimized_file_uri` so selecting an optimized preview targets its own ID. An optimized URL alone cannot authorize selecting the original recording in its place. The real backend’s optimized variants are independently selectable by their returned IDs.

## Preview and verification

Run `npm run test:voices` (Node 22.6+) for request URL/method/body, backend rejection, response mapping, selection, and audio/upload regression tests. Run `npm run build` for TypeScript and the production bundle.

Run `VITE_MOCK_DATA=true npm run dev` and open `/categories/C_Fruits/items/I_Bananas`. The preview uses the `/main` method/body and the `voices` / `variants` / `main_voice_ids` response structure. Generation/optimization use sample audio; preview uploads and selections exist only in browser memory. No content files are modified.

The desktop grid adapts from three columns to two, then one on mobile. Mobile dialogs use full-screen forms, with file-picker alternatives to drag-and-drop. Controls are translated into English, Spanish, French, and Bulgarian.
