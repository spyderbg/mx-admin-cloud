/**
 * Backend image/voice file ids are the filename stem (no directory, no extension) —
 * derivable from any file_uri without a lookup. Mirrors the id scheme used for
 * category icons, item images, and item/category voices.
 */
export function imageIdFromUri(uri: string): string {
  const filename = uri.split('/').pop() ?? ''
  const dot = filename.lastIndexOf('.')
  return dot > -1 ? filename.slice(0, dot) : filename
}
