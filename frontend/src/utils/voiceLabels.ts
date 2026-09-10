import type { Language } from '../types/viewModels'

const langFlagMap: Record<Language, string> = { en: 'us', es: 'es', fr: 'fr', bg: 'bg' }
export const langFlag = (lang: Language): string => langFlagMap[lang] || lang

const langLabelMap: Record<Language, string> = { en: 'English', es: 'Español', fr: 'Français', bg: 'Български' }
export const langLabel = (lang: Language): string => langLabelMap[lang] || lang
