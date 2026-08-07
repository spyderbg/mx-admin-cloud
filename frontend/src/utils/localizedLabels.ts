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

/** Header label for the configuration menu; unknown `lang` falls back to English. */
export function configurationLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Configuration',
    es: 'Configuración',
    fr: 'Configuration',
    bg: 'Конфигурация',
  }
  return labels[lang] ?? labels.en
}

/** Placeholder label for sidebar filtering; unknown `lang` falls back to English. */
export function filterLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Filter...',
    es: 'Filtrar...',
    fr: 'Filtrer...',
    bg: 'Филтър...',
  }
  return labels[lang] ?? labels.en
}

/** Label for the items section heading; unknown `lang` falls back to English. */
export function itemsLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Items',
    es: 'Elementos',
    fr: 'Éléments',
    bg: 'Елементи',
  }
  return labels[lang] ?? labels.en
}

/** Label for the "Item name" field; unknown `lang` falls back to English. */
export function itemNameLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Item Name',
    es: 'Nombre del elemento',
    fr: "Nom de l'élément",
    bg: 'Име на елемента',
  }
  return labels[lang] ?? labels.en
}

/** Label for the item files section; unknown `lang` falls back to English. */
export function filesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Files',
    es: 'Archivos',
    fr: 'Fichiers',
    bg: 'Файлове',
  }
  return labels[lang] ?? labels.en
}

/** Label for the item voices section; unknown `lang` falls back to English. */
export function voicesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Voices',
    es: 'Voces',
    fr: 'Voix',
    bg: 'Гласове',
  }
  return labels[lang] ?? labels.en
}

/** Label for a language column; unknown `lang` falls back to English. */
export function languageColumnLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Language',
    es: 'Idioma',
    fr: 'Langue',
    bg: 'Език',
  }
  return labels[lang] ?? labels.en
}

/** Label for a word column; unknown `lang` falls back to English. */
export function wordColumnLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Word',
    es: 'Palabra',
    fr: 'Mot',
    bg: 'Дума',
  }
  return labels[lang] ?? labels.en
}

/** Label for a type column; unknown `lang` falls back to English. */
export function typeColumnLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Type',
    es: 'Tipo',
    fr: 'Type',
    bg: 'Тип',
  }
  return labels[lang] ?? labels.en
}

/** Label for an actions column; unknown `lang` falls back to English. */
export function actionsColumnLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Actions',
    es: 'Acciones',
    fr: 'Actions',
    bg: 'Действия',
  }
  return labels[lang] ?? labels.en
}

/** Counted item label: plural form for >1, singular for exactly 1. */
export function itemCountLabel(lang: Language, count: number): string {
  const singular: Record<Language, string> = {
    en: 'item',
    es: 'elemento',
    fr: 'élément',
    bg: 'елемент',
  }
  const plural: Record<Language, string> = {
    en: 'items',
    es: 'elementos',
    fr: 'éléments',
    bg: 'елемента',
  }
  const word = count === 1 ? singular[lang] ?? singular.en : plural[lang] ?? plural.en
  return `${count} ${word}`
}

/** Search empty-state label for the category sidebar; unknown `lang` falls back to English. */
export function noCategoriesMatchLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'No categories match',
    es: 'No hay categorías que coincidan',
    fr: 'Aucune catégorie ne correspond',
    bg: 'Няма съвпадащи категории',
  }
  return labels[lang] ?? labels.en
}

/** Search empty-state label for one expanded category; unknown `lang` falls back to English. */
export function noMatchesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'No matches',
    es: 'Sin coincidencias',
    fr: 'Aucune correspondance',
    bg: 'Няма съвпадения',
  }
  return labels[lang] ?? labels.en
}

/** Label for the "Category name" field; unknown `lang` falls back to English. */
export function categoryNameLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Category Name',
    es: 'Nombre de la categoría',
    fr: 'Nom de la catégorie',
    bg: 'Име на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Label for the "Category icon" field; unknown `lang` falls back to English. */
export function categoryIconLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Category Icon',
    es: 'Icono de la categoría',
    fr: 'Icône de la catégorie',
    bg: 'Икона на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Label for the "Category voices" section; unknown `lang` falls back to English. */
export function categoryVoicesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Category Voices',
    es: 'Voces de la categoría',
    fr: 'Voix de la catégorie',
    bg: 'Гласове на категорията',
  }
  return labels[lang] ?? labels.en
}
