import type { CategoryFull, Item, Language, Voice } from '../types/viewModels'

function labelFromVoices(voices: Voice[], lang: Language): string | undefined {
  const pick = (code: Language) => voices.find((v) => v.language === code)?.name?.trim()
  const primary = pick(lang)
  if (primary) return primary
  const en = pick('en')
  if (en) return en
  return undefined
}

export function localizedCategoryLabel(category: CategoryFull, lang: Language): string {
  const voiceLabel = labelFromVoices(category.voices, lang)
  if (voiceLabel) return voiceLabel
  if (typeof (category.name as any).get === 'function') {
    return (category.name as any).get(lang, (category.name as any).en)
  }
  const dict = category.name as unknown as Record<string, string>
  return dict[lang] ?? dict['en'] ?? ''
}

export function localizedImageLabel(item: Item, lang: Language): string {
  const voiceLabel = labelFromVoices(item.voices, lang)
  if (voiceLabel) return voiceLabel
  const enLabel = labelFromVoices(item.voices, 'en')
  if (enLabel) return enLabel
  return item.name
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
