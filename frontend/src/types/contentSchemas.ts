// Auto-generated – do not edit manually
import { LocalizedString } from './localization'

/** Action performed on a category/item file (image or voice), echoed back in the response. */
export type FileAction = 'add' | 'update' | 'delete' | 'regenerate'

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
    description?: LocalizedString
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

/** Response for POST /content/categories/{category_id}/items. */
export interface CreateItemResponse {
    category: string
    item: string
    folder: string
    image_id: string
    item_id: string
    voices_generated: string[]
    voice_warnings: string[]
    image_generated?: boolean
    image_warnings?: string[]
    message: string
}

/** Response for DELETE /content/categories/archive/{name}. */
export interface DeleteArchivedCategoryResponse {
    deleted: boolean
    name?: string | null
    archive_folder?: string | null
    message: string
}

/** Request body for generating a new image variant of an item from a text prompt. */
export interface GenerateItemImageBody {
    prompt: string
    configuration_name?: string | null
}

/** Request body for generating a new take of an item voice. */
export interface GenerateItemVoiceBody {
    language: string
    text: string
    configuration_name: string
    speed?: number | null
    stability?: number | null
    similarity?: number | null
    optimize?: boolean
}

/** Response for POST /categories/{category_id}/items/{item_id}/voices/generate. */
export interface GenerateItemVoiceResponse extends ItemVoicesResponse {
    voice_id: string
    optimized_voice_ids?: string[]
    warnings?: string[]
}

/** Response for a single item image entry. */
export interface ImageResponse {
    id: string
    file_uri?: string | null
    action?: FileAction | null
    provider?: string
    parameters?: Record<string, any>
}

/** Response for GET /categories/{name}/items/{item_id}/images. */
export interface ItemImagesResponse {
    item_id: string
    name: LocalizedString
    images?: ImageResponse[]
    thumbnail_uri?: string | null
    main_image_id?: string | null
}

/** Response for GET /categories/{category_name}/items. */
export interface ItemListResponse {
    items: ItemSummary[]
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
    variants?: VoiceResponse[]
    main_voice_ids?: Record<string, string | null>
    capabilities?: VoiceCapabilities | null
}

/** Response for POST /categories/{category_id}/items/{item_id}/voices/{voice_id}/optimize. */
export interface OptimizeItemVoiceResponse extends ItemVoicesResponse {
    source_voice_id: string
    optimized_voice_ids?: string[]
    warnings?: string[]
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

/** Request body for updating a category's localized name and description. */
export interface UpdateCategoryBody {
    new_category_id?: string | null
    category_name: LocalizedString
    description: LocalizedString
}

/** Response for PATCH /content/categories/{category_id}. */
export interface UpdateCategoryResponse {
    category: string
    message: string
    category_id: string
    old_category_id: string
}

/** Request body for updating an item's localized name, and optionally its id. */
export interface UpdateItemBody {
    new_item_id?: string | null
    item_name: LocalizedString
}

/** Which voice actions the API can perform right now, so a client only offers the working ones. */
export interface VoiceCapabilities {
    multiple_voices?: boolean
    generate?: boolean
    optimize?: boolean
    select?: boolean
    presets?: boolean
    upload_formats?: string[]
}

/** A single voice entry. */
export interface VoiceResponse {
    id: string
    language: string
    type: string
    file_uri?: string | null
    action?: FileAction | null
    provider?: string
    parameters?: Record<string, any>
}
