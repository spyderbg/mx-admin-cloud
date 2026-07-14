// Auto-generated – do not edit manually

import client from './client'
import type {
  CategoryDetailsResponse,
  CategoryImagesResponse,
  CategoryListResponse,
  CategoryVoicesResponse,
  CreateCategoryBody,
  CreateCategoryResponse,
  CreateItemResponse,
  ImageResponse,
  ItemImagesResponse,
  ItemListResponse,
  ItemSummary,
  ItemVoicesResponse,
  RemoveCategoryResponse,
  RenameCategoryBody,
  RenameCategoryResponse,
  RestoreCategoryBody,
  RestoreCategoryResponse,
} from '../types/contentSchemas'

/** List all content categories */
export const categoriesList = async (params?: { includeArchived?: boolean | undefined; archivedOnly?: boolean | undefined }): Promise<CategoryListResponse> => {
  const r = await client.get<CategoryListResponse>('/content/categories', { params: { include_archived: params?.includeArchived, archived_only: params?.archivedOnly } })
  return r.data
}

/** Create a new content category */
export const categoriesCreate = async (body: CreateCategoryBody): Promise<CreateCategoryResponse> => {
  const r = await client.post<CreateCategoryResponse>('/content/categories', body)
  return r.data
}

/** Restore a category from archive */
export const categoriesRestore = async (body: RestoreCategoryBody): Promise<RestoreCategoryResponse> => {
  const r = await client.post<RestoreCategoryResponse>('/content/categories/restore', body)
  return r.data
}

/** Get details of a specific content category */
export const categoriesDetails = async (categoryId: string): Promise<CategoryDetailsResponse> => {
  const r = await client.get<CategoryDetailsResponse>(`/content/categories/${categoryId}`)
  return r.data
}

/** Rename a content category */
export const categoriesRename = async (categoryName: string, body: RenameCategoryBody): Promise<RenameCategoryResponse> => {
  const r = await client.patch<RenameCategoryResponse>(`/content/categories/${categoryName}`, body)
  return r.data
}

/** Remove a content category */
export const categoriesRemove = async (categoryName: string): Promise<RemoveCategoryResponse> => {
  const r = await client.delete<RemoveCategoryResponse>(`/content/categories/${categoryName}`)
  return r.data
}

/** List all images in a category */
export const categoriesImagesList = async (categoryId: string): Promise<CategoryImagesResponse> => {
  const r = await client.get<CategoryImagesResponse>(`/content/categories/${categoryId}/images`)
  return r.data
}

/** Add an image to a category */
export const categoriesImagesAdd = async (categoryId: string, formData: FormData): Promise<ImageResponse> => {
  const r = await client.post<ImageResponse>(`/content/categories/${categoryId}/images`, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}

/** Replace a category image file */
export const categoriesImagesUpdate = async (categoryId: string, imageId: string, formData: FormData): Promise<ImageResponse> => {
  const r = await client.put<ImageResponse>(`/content/categories/${categoryId}/images/${imageId}`, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}

/** List voices for a category */
export const categoriesVoicesList = async (categoryName: string): Promise<CategoryVoicesResponse> => {
  const r = await client.get<CategoryVoicesResponse>(`/content/categories/${categoryName}/voices`)
  return r.data
}

/** List all items in a category */
export const itemsList = async (categoryName: string): Promise<ItemListResponse> => {
  const r = await client.get<ItemListResponse>(`/content/categories/${categoryName}/items`)
  return r.data
}

/** Get a single item by ID */
export const itemsGet = async (categoryName: string, itemId: string): Promise<ItemSummary> => {
  const r = await client.get<ItemSummary>(`/content/categories/${categoryName}/items/${itemId}`)
  return r.data
}

/** List image variants for an item */
export const itemsImagesList = async (categoryName: string, itemId: string): Promise<ItemImagesResponse> => {
  const r = await client.get<ItemImagesResponse>(`/content/categories/${categoryName}/items/${itemId}/images`)
  return r.data
}

/** List voices for an item */
export const itemsVoicesList = async (categoryName: string, itemId: string): Promise<ItemVoicesResponse> => {
  const r = await client.get<ItemVoicesResponse>(`/content/categories/${categoryName}/items/${itemId}/voices`)
  return r.data
}

/** Create a new content item */
export const createItemEndpoint = async (formData: FormData): Promise<CreateItemResponse> => {
  const r = await client.post<CreateItemResponse>('/content/items', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}
