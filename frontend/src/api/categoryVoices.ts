import client from './client'

export interface CategoryVoiceListResponse {
  voices?: string[]
  [key: string]: unknown
}

export const listCategoryVoices = async (categoryName: string): Promise<CategoryVoiceListResponse> => {
  const r = await client.get<CategoryVoiceListResponse>(`/content/categories/${categoryName}/voices`)
  return r.data
}

export const addCategoryVoice = async (categoryName: string, body: unknown): Promise<unknown> => {
  const r = await client.post(`/content/categories/${categoryName}/voices`, body)
  return r.data
}

export const updateCategoryVoice = async (categoryName: string, voiceId: string, body: unknown): Promise<unknown> => {
  const r = await client.put(`/content/categories/${categoryName}/voices/${voiceId}`, body)
  return r.data
}

export const deleteCategoryVoice = async (categoryName: string, voiceId: string): Promise<void> => {
  await client.delete(`/content/categories/${categoryName}/voices/${voiceId}`)
}
