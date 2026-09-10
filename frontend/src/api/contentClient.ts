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
  DeleteArchivedCategoryResponse,
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
  UpdateItemBody,
  VoiceResponse,
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

/** Delete a category from archive */
export const categoriesArchiveDelete = async (archiveName: string): Promise<DeleteArchivedCategoryResponse> => {
  const r = await client.delete<DeleteArchivedCategoryResponse>(`/content/categories/archive/${archiveName}`)
  return r.data
}

/** Get details of a specific content category */
export const categoriesDetails = async (categoryId: string): Promise<CategoryDetailsResponse> => {
  const r = await client.get<CategoryDetailsResponse>(`/content/categories/${categoryId}`)
  return r.data
}

/** Update a content category's name and description */
export const categoriesRename = async (categoryId: string, body: RenameCategoryBody): Promise<RenameCategoryResponse> => {
  const r = await client.patch<RenameCategoryResponse>(`/content/categories/${categoryId}`, body)
  return r.data
}

/** Remove a content category */
export const categoriesRemove = async (categoryId: string): Promise<RemoveCategoryResponse> => {
  const r = await client.delete<RemoveCategoryResponse>(`/content/categories/${categoryId}`)
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

/** Delete category image */
export const categoriesImagesDelete = async (categoryId: string, imageId: string): Promise<ImageResponse> => {
  const r = await client.delete<ImageResponse>(`/content/categories/${categoryId}/images/${imageId}`)
  return r.data
}

/** Create or replace a category's icon/thumbnail image */
export const categoriesIconUpdate = async (categoryId: string, formData: FormData): Promise<ImageResponse> => {
  const r = await client.post<ImageResponse>(`/content/categories/${categoryId}/icon`, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}

/** Delete a category's icon/thumbnail image */
export const categoriesIconDelete = async (categoryId: string): Promise<ImageResponse> => {
  const r = await client.delete<ImageResponse>(`/content/categories/${categoryId}/icon`)
  return r.data
}

/** List voices for a category */
export const categoriesVoicesList = async (categoryId: string): Promise<CategoryVoicesResponse> => {
  const r = await client.get<CategoryVoicesResponse>(`/content/categories/${categoryId}/voices`)
  return r.data
}

/** Add a voice to a category */
export const categoriesVoicesAdd = async (categoryId: string, formData: FormData): Promise<VoiceResponse> => {
  const r = await client.post<VoiceResponse>(`/content/categories/${categoryId}/voices`, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}

/** Replace audio for a category voice */
export const categoriesVoicesUpdate = async (categoryId: string, voiceId: string, formData: FormData): Promise<VoiceResponse> => {
  const r = await client.put<VoiceResponse>(`/content/categories/${categoryId}/voices/${voiceId}`, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}

/** Delete a voice from a category */
export const categoriesVoicesDelete = async (categoryId: string, voiceId: string): Promise<VoiceResponse> => {
  const r = await client.delete<VoiceResponse>(`/content/categories/${categoryId}/voices/${voiceId}`)
  return r.data
}

/** Regenerate a voice for a category */
export const categoriesVoicesRegenerate = async (categoryId: string, voiceId: string): Promise<VoiceResponse> => {
  const r = await client.post<VoiceResponse>(`/content/categories/${categoryId}/voices/${voiceId}/regenerate`)
  return r.data
}

/** List all items in a category */
export const itemsList = async (categoryId: string): Promise<ItemListResponse> => {
  const r = await client.get<ItemListResponse>(`/content/categories/${categoryId}/items`)
  return r.data
}

/** Create a new item in a category */
export const itemsCreate = async (categoryId: string, formData: FormData): Promise<CreateItemResponse> => {
  const r = await client.post<CreateItemResponse>(`/content/categories/${categoryId}/items`, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}

/** Get a single item by ID */
export const itemsGet = async (categoryId: string, itemId: string): Promise<ItemSummary> => {
  const r = await client.get<ItemSummary>(`/content/categories/${categoryId}/items/${itemId}`)
  return r.data
}

/** Update an item in a category */
export const itemsUpdate = async (categoryId: string, itemId: string, body: UpdateItemBody): Promise<ItemSummary> => {
  const r = await client.put<ItemSummary>(`/content/categories/${categoryId}/items/${itemId}`, body)
  return r.data
}

/** Delete an item from a category */
export const itemsDelete = async (categoryId: string, itemId: string): Promise<void> => {
  await client.delete(`/content/categories/${categoryId}/items/${itemId}`)
}

/** List image variants for an item */
export const itemsImagesList = async (categoryId: string, itemId: string): Promise<ItemImagesResponse> => {
  const r = await client.get<ItemImagesResponse>(`/content/categories/${categoryId}/items/${itemId}/images`)
  return r.data
}

/** Add an image file variant to an item */
export const itemsImagesAdd = async (categoryId: string, itemId: string, formData: FormData): Promise<ImageResponse> => {
  const r = await client.post<ImageResponse>(`/content/categories/${categoryId}/items/${itemId}/images`, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}

/** Replace a specific image file variant */
export const itemsImagesUpdate = async (categoryId: string, itemId: string, imageId: string, formData: FormData): Promise<ImageResponse> => {
  const r = await client.put<ImageResponse>(`/content/categories/${categoryId}/items/${itemId}/images/${imageId}`, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}

/** Delete a file variant from an item */
export const itemsImagesDelete = async (categoryId: string, itemId: string, imageId: string): Promise<ImageResponse> => {
  const r = await client.delete<ImageResponse>(`/content/categories/${categoryId}/items/${itemId}/images/${imageId}`)
  return r.data
}

/** List voices for an item */
export const itemsVoicesList = async (categoryId: string, itemId: string): Promise<ItemVoicesResponse> => {
  const r = await client.get<ItemVoicesResponse>(`/content/categories/${categoryId}/items/${itemId}/voices`)
  return r.data
}

/** Add a voice to an item */
export const itemsVoicesAdd = async (categoryId: string, itemId: string, formData: FormData): Promise<VoiceResponse> => {
  const r = await client.post<VoiceResponse>(`/content/categories/${categoryId}/items/${itemId}/voices`, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}

/** Update voice for an item */
export const itemsVoicesUpdate = async (categoryId: string, itemId: string, voiceId: string, formData: FormData): Promise<VoiceResponse> => {
  const r = await client.put<VoiceResponse>(`/content/categories/${categoryId}/items/${itemId}/voices/${voiceId}`, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}

/** Delete a voice from an item */
export const itemsVoicesDelete = async (categoryId: string, itemId: string, voiceId: string): Promise<VoiceResponse> => {
  const r = await client.delete<VoiceResponse>(`/content/categories/${categoryId}/items/${itemId}/voices/${voiceId}`)
  return r.data
}

/** Regenerate a voice for an item */
export const itemsVoicesRegenerate = async (categoryId: string, itemId: string, voiceId: string): Promise<VoiceResponse> => {
  const r = await client.post<VoiceResponse>(`/content/categories/${categoryId}/items/${itemId}/voices/${voiceId}/regenerate`)
  return r.data
}

/** Create a new content item */
export const createItemEndpoint = async (formData: FormData): Promise<CreateItemResponse> => {
  const r = await client.post<CreateItemResponse>('/content/items', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}
