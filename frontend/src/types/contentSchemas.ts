// Auto-generated – do not edit manually
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

/** Response for GET /content/categories/{name}.

    For 'images_by_lang' maps each language code to its CSV-derived word list,
    so any number of languages can be returned without changing the schema. */
export interface CategoryDetailResponse {
    id: string
    name: string
    items?: string[]
    images_ids?: string[] | null
    images_by_lang?: Record<string, string[]>
}

/** Response for GET /categories/{name}/images. */
export interface CategoryImagesResponse {
    category: string
    items?: ItemDetailResponse[]
}

/** Response for GET /content/categories. */
export interface CategoryListResponse {
    categories: CategorySummary[]
    archived?: ArchivedEntry[] | null
    error?: string | null
}

/** Summary of a single category returned in the list endpoint. */
export interface CategorySummary {
    name: string
    items_count: number
}

/** Response for GET /categories/{name}/voices. */
export interface CategoryVoicesResponse {
    category: string
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

/** Detailed response for a single item. */
export interface ItemDetailResponse {
    id: string
    name: string
    files?: string[]
    thumbnail_url?: string | null
    voices?: VoiceResponse[]
}

/** Response for GET /categories/{name}/items/{item_id}/images. */
export interface ItemImagesResponse {
    item_id: string
    files?: string[]
    thumbnail_url?: string | null
}

/** Response for GET /categories/{category_name}/items. */
export interface ItemListResponse {
    items: string[]
}

/** Response for GET /categories/{name}/items/{item_id}/voices. */
export interface ItemVoicesResponse {
    item_id: string
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
    name: string
    language: string
    type: string
    audio_url?: string | null
}
