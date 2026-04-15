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

export interface Image {
  id: string
  name: string
  files: string[]
  voices: Voice[]
  thumbnailUrl: string
}

export interface Category {
  id: string
  name: string
  description: string
  iconUrl: string
  voices: Voice[]
  images: Image[]
}

export interface User {
  name: string
  role: string
  avatarUrl: string
}
