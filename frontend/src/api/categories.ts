import client from './client'
import { SUPPORTED_LANGUAGES } from '../types'
import type { CategoryDetail, CategorySummary } from '../types'

export interface CategoryListParams {
  includeArchived?: boolean
  archivedOnly?: boolean
}

export interface CreateCategoryBody {
  name: string
}

export interface RenameCategoryBody {
  name: string
}

export interface RestoreCategoryBody {
  name: string
}

export interface CreateCategoryResponse {
  category: string
  message: string
}

export interface RenameCategoryResponse {
  category: string
  message: string
}

export interface RemoveCategoryResponse {
  archived?: boolean | null
  removed?: boolean | null
  name: string
  archive_folder?: string | null
  destination?: string | null
  message: string
}

export interface RestoreCategoryResponse {
  restored: boolean
  name?: string | null
  from_archive?: string | null
  message: string
}

export const listCategories = async (params: CategoryListParams = {}): Promise<CategorySummary> => {
  const r = await client.get<CategorySummary>('/content/categories', {
    params: {
      include_archived: params.includeArchived ?? false,
      archived_only: params.archivedOnly ?? false,
    },
  })
  return r.data
}

export const createCategory = async (body: CreateCategoryBody): Promise<CreateCategoryResponse> => {
  const r = await client.post<CreateCategoryResponse>('/content/categories', body)
  return r.data
}

export const restoreCategory = async (body: RestoreCategoryBody): Promise<RestoreCategoryResponse> => {
  const r = await client.post<RestoreCategoryResponse>('/content/categories/restore', body)
  return r.data
}

interface CategoryDetailApiResponse {
  name: string
  items: string[]
  images_ids?: string[] | null
  images_by_lang?: Record<string, string[]> | null
}

export const getCategoryDetail = async (name: string): Promise<CategoryDetail> => {
  const r = await client.get<CategoryDetailApiResponse>(`/content/categories/${name}`)
  const data = r.data
  const imagesByLanguage: CategoryDetail['imagesByLanguage'] = {}
  for (const lang of SUPPORTED_LANGUAGES) {
    const words = data.images_by_lang?.[lang]
    if (Array.isArray(words)) imagesByLanguage[lang] = words
  }

  return {
    name: data.name,
    items: data.items ?? [],
    imagesIds: data.images_ids ?? null,
    imagesByLanguage,
  }
}

export const updateCategory = async (name: string): Promise<unknown> => {
  const r = await client.put(`/content/categories/${name}`)
  return r.data
}

export const renameCategory = async (name: string, body: RenameCategoryBody): Promise<RenameCategoryResponse> => {
  const r = await client.patch<RenameCategoryResponse>(`/content/categories/${name}`, body)
  return r.data
}

export const removeCategory = async (name: string): Promise<RemoveCategoryResponse> => {
  const r = await client.delete<RemoveCategoryResponse>(`/content/categories/${name}`)
  return r.data
}
