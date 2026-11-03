import { ref } from 'vue'
import { itemsExport } from '../api/contentClient'

/** Use a local filename rather than trusting a server-supplied path or header. */
export function categoryItemsCsvFilename(categoryId: string): string {
  const safeId = categoryId.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 100) || 'category'
  return `${safeId}-items.csv`
}

export function useCategoryItemsExport(onError: () => void) {
  const exporting = ref(false)

  async function exportItems(categoryId: string): Promise<void> {
    if (exporting.value) return
    exporting.value = true
    try {
      const blob = await itemsExport(categoryId)
      const url = URL.createObjectURL(blob)
      let anchor: HTMLAnchorElement | undefined
      try {
        anchor = document.createElement('a')
        anchor.href = url
        anchor.download = categoryItemsCsvFilename(categoryId)
        document.body.appendChild(anchor)
        anchor.click()
      } finally {
        anchor?.remove()
        // Let the browser start consuming the URL before releasing it.
        setTimeout(() => URL.revokeObjectURL(url), 0)
      }
    } catch {
      onError()
    } finally {
      exporting.value = false
    }
  }

  return { exporting, exportItems }
}
