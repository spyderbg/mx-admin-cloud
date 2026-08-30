/**
 * Category/item file URIs are stable across re-uploads (same filename), so the browser
 * keeps serving the previously cached file unless a fresh request is forced with a
 * changing query param.
 */
export function cacheBustedUri(uri: string | null | undefined, version: number): string {
  if (!uri) return ''
  if (!version) return uri
  const sep = uri.includes('?') ? '&' : '?'
  return `${uri}${sep}v=${version}`
}
