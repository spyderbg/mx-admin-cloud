// Auto-generated – do not edit manually
import { LocalizedString } from './localization'

/** Response for archiving (or removing via archive) a category. */
export interface ArchiveCategoryResponse {
    archived?: boolean | null
    removed?: boolean | null
    name: string
    archive_folder?: string | null
    destination?: string | null
    message: string
}

/** Single archived category entry. */
export interface ArchivedEntry {
    base_name: string
    archive_folder: string
    archived_at: number
}

/** Response for GET /content/categories/{name}. */
export interface CategoryDetailsResponse {
    id: string
    name: LocalizedString
    description?: string
    items?: ItemSummary[]
}

/** Response for GET /categories/{name}/images. */
export interface CategoryImagesResponse {
    category_id: string
    name: LocalizedString
    images?: ImageResponse[]
}

/** Response for GET /content/categories. */
export interface CategoryListResponse {
    categories: CategorySummary[]
    archived?: ArchivedEntry[] | null
    error?: string | null
}

/** Summary of a single category returned in the list endpoint. */
export interface CategorySummary {
    id: string
    name: LocalizedString
    items_count: number
    thumbnail_uri?: string | null
}

/** Response for GET /categories/{name}/voices. */
export interface CategoryVoicesResponse {
    category_id: string
    name: LocalizedString
    voices?: VoiceResponse[]
}

/** Request body for creating a new category. */
export interface CreateCategoryBody {
    name: string
}

/** Response for POST /content/categories. */
export interface CreateCategoryResponse {
    category: string
    message: string
}

/** Response for POST /content/items. */
export interface CreateItemResponse {
    category: string
    item: string
    folder: string
    image_id: string
}

/** Response for a single item image entry. */
export interface ImageResponse {
    id: string
    file_uri?: string | null
}

/** Response for GET /categories/{name}/items/{item_id}/images. */
export interface ItemImagesResponse {
    item_id: string
    files?: string[]
    thumbnail_uri?: string | null
}

/** Response for GET /categories/{category_name}/items. */
export interface ItemListResponse {
    items: string[]
}

/** Item entry returned inside CategoryDetailsResponse. */
export interface ItemSummary {
    id: string
    name: LocalizedString
    thumbnail_uri?: string | null
    images_count?: number
}

/** Response for GET /categories/{name}/items/{item_id}/voices. */
export interface ItemVoicesResponse {
    item_id: string
    name: LocalizedString
    voices?: VoiceResponse[]
}

/** Response for DELETE /content/categories/{name}.

    Maybe an archive result or a permanent delete result, so both
    "archived" and "removed" are optional. */
export interface RemoveCategoryResponse {
    archived?: boolean | null
    removed?: boolean | null
    name: string
    archive_folder?: string | null
    destination?: string | null
    message: string
}

/** Request body for renaming a category. */
export interface RenameCategoryBody {
    name: string
}

/** Response for PATCH /content/categories/{name}. */
export interface RenameCategoryResponse {
    category: string
    message: string
}

/** Request body for restoring a category. */
export interface RestoreCategoryBody {
    name: string
}

/** Response for POST /content/categories/restore. */
export interface RestoreCategoryResponse {
    restored: boolean
    name?: string | null
    from_archive?: string | null
    message: string
}

/** A single voice entry. */
export interface VoiceResponse {
    id: string
    language: string
    type: string
    file_uri?: string | null
}
