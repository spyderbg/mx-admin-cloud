// Hand-written UI view-model types.
//
// Backend DTOs live in the generated `*Schemas.ts` files (do not edit those).
// The API layer (`src/api/cache.ts`) maps backend DTOs → these camelCase UI
// types. `Language`/`SUPPORTED_LANGUAGES` are hand-maintained here because the
// backend exposes them as plain strings (see backend `core/config.py` /
// `core/languages.py`), so the schema generator cannot emit them.

export type Language = 'en' | 'es' | 'fr' | 'bg'

export const SUPPORTED_LANGUAGES: Language[] = ['en', 'es', 'fr', 'bg']

export interface Image {
    id: string
    name: string
    language: Language
    iconUri: string
}

export interface Voice {
    id: string
    name: string
    language: Language
    type: string
    iconUri: string
    /** When absent or empty, the voice is considered missing and play is disabled. */
    audioUri?: string
}

export interface Category {
    /** UI-only identifier; backend identifies categories by `name`. */
    id: string
    name: string
    description: string
    /** UI-only array of images isn't provided by backend category responses. */
    images: Image[]
    archived?: boolean
    voices: Voice[]
    items: Item[]
}

export interface Item {
    id: string
    name: string
    images: string[]
    voices: Voice[]
    thumbnailUri: string
}

export interface User {
    name: string
    role: string
    avatarUri: string
}
