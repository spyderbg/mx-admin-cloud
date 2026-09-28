# Item Voices: frontend integration and remaining API support

The language accordion/card layout and mobile dialogs are implemented in the frontend. Selection uses the generated `itemsVoicesSetMain` client. This integration changes no backend code or generated schemas.

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
| `POST /{voice_id}/regenerate` | Overwrites the current recording using the active configuration. The new Generate dialog does not substitute this operation for creating a new take. |
| `POST /{voice_id}/optimize` | Now implemented in the backend: returns the updated voice lists, `source_voice_id`, `optimized_voice_ids`, and warnings. Adapting the optimization UI from its inline preview contract to this response remains separate work. |

Uploads remain restricted to MP3 unless the API advertises additional formats, because the existing implementation writes bytes to an `.mp3` filename. Listing multiple variants does not imply that the upload endpoint can append recordings.

## Missing endpoints and extensions

The following paths are relative to the base path above. Generation/preset contracts are handwritten in `src/api/itemVoicesClient.ts` / `src/types/itemVoices.ts` and simulated in the frontend MSW preview.

| Endpoint | Required behavior |
| --- | --- |
| `GET /presets?language=en` | Return per-language base voices: `id`, `provider`, `name`, `model`, `language`, `speed`, optional `stability`/`similarity` defaults. |
| `POST /generate` | Create a new saved candidate. Body: `language`, `text`, `preset_id`, `speed`, optional `stability`/`similarity`, `optimize`. Preserve the current selection. |
| Extend `POST /voices` | Append additional uploads for a language without overwriting the current recording; preserve the format or transcode it. |
| Optional `capabilities` on `GET /voices` | Advertise available generation, optimization, presets, multiple uploads, and supported upload formats. These flags default to false and formats to MP3. Selection already works with the implemented list contract. |

Existing internal generator endpoints are not substitutes for saved per-item candidates with per-request settings. The browser should not infer server filesystem paths from public audio URLs.

The mock preview retains inline Original/Optimized tabs. These use an explicit `optimized_voice_id` alongside `optimized_file_uri` so selecting an optimized preview targets its own ID. An optimized URL alone cannot authorize selecting the original recording in its place. The real backend’s optimized variants are independently selectable by their returned IDs.

## Preview and verification

Run `npm run test:voices` (Node 22.6+) for request URL/method/body, backend rejection, response mapping, selection, and audio/upload regression tests. Run `npm run build` for TypeScript and the production bundle.

Run `VITE_MOCK_DATA=true npm run dev` and open `/categories/C_Fruits/items/I_Bananas`. The preview uses the `/main` method/body and the `voices` / `variants` / `main_voice_ids` response structure. Generation/optimization use sample audio; preview uploads and selections exist only in browser memory. The visible preview notice identifies this simulation. No content files are modified.

The desktop grid adapts from three columns to two, then one on mobile. Mobile dialogs use full-screen forms, with file-picker alternatives to drag-and-drop. Controls are translated into English, Spanish, French, and Bulgarian.
