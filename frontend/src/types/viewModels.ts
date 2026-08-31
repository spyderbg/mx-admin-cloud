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

/** A category that lives in the archive rather than the content directory. */
export interface ArchivedCategory {
    /** Folder name the category is restored back to, e.g. 'foods'. */
    baseName: string
    /** Archive snapshot folder, e.g. 'foods_1738456789'. */
    archiveFolder: string
    /** Unix seconds, taken from the archive folder suffix. */
    archivedAt: number
    /** Optional number of items preserved in the archive snapshot. */
    itemsCount?: number
    /** Optional thumbnail URI for archive list previews. */
    thumbnailUri?: string
    /** Optional unix seconds for the category creation date. */
    createdAt?: number
}
