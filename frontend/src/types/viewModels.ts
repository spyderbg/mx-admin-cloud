// UI view-model types for business code (cache, views, utils).
// Mock-data-only types live-in types/viewModels.ts

import { LocalizedString } from './localization'
export { LocalizedString }

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
    /** When absent or empty, the voice is considered missing and play is disabled. */
    fileUri?: string
}

export interface Item {
    id: string
    name: string
    images: string[]
    voices: Voice[]
    thumbnailUri: string
    imagesCount?: number
}

export interface CategoryFull {
    id: string
    name: LocalizedString
    description: LocalizedString
    thumbnail_uri?: string | null
    items_count?: number
    images: Image[]
    voices: Voice[]
    items: Item[]
}
