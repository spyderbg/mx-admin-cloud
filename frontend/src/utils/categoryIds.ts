/**
 * Category id ⇄ folder name helpers.
 *
 * The backend registers categories as ids ('C_AtSchool') but stores them in lowercase
 * snake_case folders ('at_school'), and archived entries are reported by folder name.
 * These mirror the backend derivations in FileSystemContentProvider.
 */

/** 'C_AtSchool' → 'at_school'. Mirrors the backend's _folder_name_from_category_id(). */
export function folderNameFromCategoryId(categoryId: string): string {
  const separator = categoryId.indexOf('_')
  const remainder = separator === -1 ? categoryId : categoryId.slice(separator + 1)
  return (remainder || categoryId).replace(/(?!^)([A-Z])/g, '_$1').toLowerCase()
}

/**
 * Normalize a typed category name to the folder name the backend will create.
 * Mirrors the backend's _normalize_category_name().
 */
export function normalizeCategoryName(name: string): string {
  return name.trim().replace(/[\s-]+/g, '_').toLowerCase()
}

/** 'at_school' → 'C_AtSchool'. Mirrors the id the backend assigns on create. */
export function categoryIdFromFolderName(folderName: string): string {
  const pascal = folderName
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join('')
  return `C_${pascal}`
}
