import client from './client'
import type { Category } from '../types'

export const getCategories = async (): Promise<Category[]> => {
  const r = await client.get<Category[]>('/content/categories')
  return r.data
}

export const getCategory = async (id: string): Promise<Category> => {
  const r = await client.get<Category>(`/content/categories/${id}`)
  return r.data
}
