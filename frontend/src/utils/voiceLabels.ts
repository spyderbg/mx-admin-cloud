import type { Language } from '../types/viewModels'

const langFlagMap: Record<Language, string> = { en: 'us', es: 'es', fr: 'fr', bg: 'bg' }
export const langFlag = (lang: Language): string => langFlagMap[lang] || lang

const langLabelMap: Record<Language, Record<Language, string>> = {
  en: { en: 'English', es: 'Spanish', fr: 'French', bg: 'Bulgarian' },
  es: { en: 'Inglés', es: 'Español', fr: 'Francés', bg: 'Búlgaro' },
  fr: { en: 'Anglais', es: 'Espagnol', fr: 'Français', bg: 'Bulgare' },
  bg: { en: 'Английски', es: 'Испански', fr: 'Френски', bg: 'Български' },
}
export const langLabel = (lang: Language, displayLanguage: Language = 'en'): string =>
  langLabelMap[displayLanguage]?.[lang] || langLabelMap.en[lang] || lang
