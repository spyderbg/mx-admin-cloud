import client from './client'

export interface ItemVoicesListResponse {
  voices?: Array<{
    id?: string
    name?: string
    language?: string
    type?: string
    audio_url?: string
  }>
  [key: string]: unknown
}

export const listItemVoices = async (categoryName: string, itemId: string): Promise<ItemVoicesListResponse> => {
  const r = await client.get<ItemVoicesListResponse>(`/content/categories/${categoryName}/items/${itemId}/voices`)
  return r.data
}

export const updateItemVoice = async (
  categoryName: string,
  itemId: string,
  voiceId: string,
  body: unknown,
): Promise<unknown> => {
  const r = await client.put(`/content/categories/${categoryName}/items/${itemId}/voices/${voiceId}`, body)
  return r.data
}

export const deleteItemVoice = async (categoryName: string, itemId: string, voiceId: string): Promise<void> => {
  await client.delete(`/content/categories/${categoryName}/items/${itemId}/voices/${voiceId}`)
}

export const uploadItemVoiceAudio = async (
  categoryName: string,
  itemId: string,
  voiceId: string,
  body: unknown,
): Promise<unknown> => {
  const r = await client.put(`/content/categories/${categoryName}/items/${itemId}/voices/${voiceId}/audio`, body)
  return r.data
}
