/**
 * Category/item file URIs are stable across re-uploads (same filename), so the browser
 * keeps serving the previously cached file unless a fresh request is forced with a
 * changing query param.
 */
export function cacheBustedUri(uri: string | null | undefined, version: number): string {
  if (!uri) return ''
  // Browser upload previews are opaque URLs, not cacheable server filenames.
  if (uri.startsWith('blob:') || uri.startsWith('data:')) return uri
  if (!version) return uri
  const fragmentIndex = uri.indexOf('#')
  const base = fragmentIndex === -1 ? uri : uri.slice(0, fragmentIndex)
  const fragment = fragmentIndex === -1 ? '' : uri.slice(fragmentIndex)
  const sep = base.includes('?') ? '&' : '?'
  return `${base}${sep}v=${version}${fragment}`
}
