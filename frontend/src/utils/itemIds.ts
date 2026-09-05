/**
 * Item id helpers mirroring the backend derivations in ContentProvider
 * (backend/src/services/content/models.py).
 */

/** 'hot chocolate' → 'I_HotChocolate'. Mirrors the backend's _english_word_to_image_id(). */
export function itemIdFromEnglishWord(word: string): string {
  const pascal = word
    .trim()
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join('')
  return pascal ? `I_${pascal}` : ''
}
