export type Language = 'en' | 'es' | 'fr' | 'bg'

export const SUPPORTED_LANGUAGES: Language[] = ['en', 'es', 'fr', 'bg']

export interface Voice {
  id: string
  name: string
  language: Language
  type: string
  iconUrl: string
  /** When absent or empty, the voice is considered missing and play is disabled. */
  audioUrl?: string
}

export interface Item {
  id: string
  name: string
  files: string[]
  voices: Voice[]
  thumbnailUrl: string
}

export interface Category {
  /** UI-only identifier; backend identifies categories by `name`. */
  id: string
  name: string
  description: string
  /** UI-only field not provided by backend category responses. */
  iconUrl: string
  archived?: boolean
  voices: Voice[]
  items: Item[]
}

export interface User {
  name: string
  role: string
  avatarUrl: string
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
  objects: string[]
  imagesNames?: string[] | null
  imagesByLanguage?: Partial<Record<Language, string[]>>
}
