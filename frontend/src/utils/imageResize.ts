export const MAX_ICON_DIMENSION = 1024

/**
 * Downscales an image file so neither dimension exceeds `maxDimension`, preserving aspect
 * ratio. Images already within bounds pass through unchanged. Falls back to the original
 * file if decoding/resizing fails for any reason (e.g. unsupported format).
 */
export async function resizeImageToMaxDimension(
  file: File,
  maxDimension: number = MAX_ICON_DIMENSION,
): Promise<File> {
  try {
    const bitmap = await createImageBitmap(file)
    if (bitmap.width <= maxDimension && bitmap.height <= maxDimension) {
      bitmap.close()
      return file
    }

    const scale = maxDimension / Math.max(bitmap.width, bitmap.height)
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(bitmap.width * scale)
    canvas.height = Math.round(bitmap.height * scale)
    const ctx = canvas.getContext('2d')
    if (!ctx) {
      bitmap.close()
      return file
    }
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    bitmap.close()

    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, file.type || 'image/png'))
    return blob ? new File([blob], file.name, { type: blob.type }) : file
  } catch {
    return file
  }
}
