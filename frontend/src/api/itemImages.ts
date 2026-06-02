import client from './client'

export interface ItemImagesListResponse {
  images?: string[]
  [key: string]: unknown
}

export const listItemImages = async (categoryName: string, itemId: string): Promise<ItemImagesListResponse> => {
  const r = await client.get<ItemImagesListResponse>(`/content/categories/${categoryName}/items/${itemId}/images`)
  return r.data
}

export const addItemImage = async (categoryName: string, itemId: string, body: unknown): Promise<unknown> => {
  const r = await client.post(`/content/categories/${categoryName}/items/${itemId}/images`, body)
  return r.data
}

export const replaceItemImage = async (
  categoryName: string,
  itemId: string,
  imageIndex: number,
  body: unknown,
): Promise<unknown> => {
  const r = await client.put(`/content/categories/${categoryName}/items/${itemId}/images/${imageIndex}`, body)
  return r.data
}

export const deleteItemImage = async (categoryName: string, itemId: string, imageIndex: number): Promise<void> => {
  await client.delete(`/content/categories/${categoryName}/items/${itemId}/images/${imageIndex}`)
}
