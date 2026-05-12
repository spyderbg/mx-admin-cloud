import client from './client'

export interface ItemListResponse {
  items: string[]
}

export const listItems = async (categoryName: string): Promise<ItemListResponse> => {
  const r = await client.get<ItemListResponse>(`/content/categories/${categoryName}/items`)
  return r.data
}

export const getItem = async (categoryName: string, itemId: string): Promise<unknown> => {
  const r = await client.get(`/content/categories/${categoryName}/items/${itemId}`)
  return r.data
}

export const updateItem = async (categoryName: string, itemId: string, body: unknown): Promise<unknown> => {
  const r = await client.put(`/content/categories/${categoryName}/items/${itemId}`, body)
  return r.data
}

export const deleteItem = async (categoryName: string, itemId: string): Promise<void> => {
  await client.delete(`/content/categories/${categoryName}/items/${itemId}`)
}

export const createItem = async (formData: FormData): Promise<unknown> => {
  const r = await client.post('/content/items', formData)
  return r.data
}
