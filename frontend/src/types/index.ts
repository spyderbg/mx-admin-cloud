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
    /** UI-only field isn't provided by backend category responses. */
    icon: Image
    archived?: boolean
    voices: Voice[]
    items: Item[]
}

export interface Item {
    id: string
    name: string
    files: string[]
    voices: Voice[]
    thumbnailUri: string
}

export interface User {
    name: string
    role: string
    avatarUri: string
}

export interface ArchivedEntry {
    baseName: string
    archiveFolder: string
    archivedAt: number
}

export interface CategorySummary {
    categories: string[]
    archived?: ArchivedEntry[] | null
    error?: string | null
}

export interface CategoryDetail {
    name: string
    items: string[]
    imagesIds?: string[] | null
    imagesByLanguage?: Partial<Record<Language, string[]>>
}
