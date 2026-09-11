// Auto-generated – do not edit manually
/** Serialized language-code/text pair used in translation responses. */
export interface LangCodeText {
    lang_code: string
    text: string
}

/** Response body for the translation file endpoint. */
export interface TranslateFileResponse {
    results: Record<string, string>[]
}

/** Request body for the translation endpoint. */
export interface TranslateRequest {
    text: string
    input_lang_code?: string | null
    target_lang_code: string
    category?: string | null
}

/** Response body for the translation endpoint. */
export interface TranslateResponse {
    success: boolean
    lang_text: LangCodeText
    errors?: string[]
}

/** One text unit loaded for "/internal/translate-file" (HTTP contract for the translation flow). */
export interface TranslationFileSourceWord {
    text: string
    source_language_code: string
    filename_text: string
    expected_text: string
    row_index: number
    column_index?: number | null
    source_file: string
    source_type: string
}
