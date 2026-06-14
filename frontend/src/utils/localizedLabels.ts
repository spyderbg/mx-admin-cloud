import type { CategoryFull, Item, Language, Voice } from '../types'

function labelFromVoices(voices: Voice[], lang: Language): string | undefined {
  const pick = (code: Language) => voices.find((v) => v.language === code)?.name?.trim()
  const primary = pick(lang)
  if (primary) return primary
  const en = pick('en')
  if (en) return en
  return undefined
}

export function localizedCategoryLabel(category: CategoryFull, lang: Language): string {
  return labelFromVoices(category.voices, lang) ?? category.name
}

export function localizedImageLabel(item: Item, lang: Language): string {
  return labelFromVoices(item.voices, lang) ?? item.name
}

/** The first segment of category breadcrumbs; unknown `lang` falls back to English. */
export function breadcrumbCategoriesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Categories',
    es: 'Categorías',
    fr: 'Catégories',
    bg: 'Категории',
  }
  return labels[lang] ?? labels.en
}
