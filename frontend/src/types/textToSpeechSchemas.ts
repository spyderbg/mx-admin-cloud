// Auto-generated – do not edit manually
/** Body for a single TTS generation request. */
export interface GenerateRequest {
    text: string
    language_code: string
    language_name: string
    filename_text: string
    expected_text: string
    source_file: string
    source_type: string
    output_format?: string
    row_index: number
    column_index?: number | null
    request_id?: string | null
}

/** Body for single-word TTS generation. */
export interface GenerateWordRequest {
    word: string
    lang: string
}

/** Metadata for one configured TTS provider (GET /internal/generators). */
export interface GeneratorProviderInfo {
    name: string
    enabled: boolean
    config_path: string
    generate: Record<string, any>
    evaluate_enabled: boolean
}

/** Serialized generated-audio payload returned by the generate endpoint. */
export interface TtsGenerateResult {
    provider: string
    request_id: string
    language_code: string
    language_name: string
    expected_text: string
    filename_text: string
    output_format: string
    raw_path: string
    raw_extension: string
    latency_seconds: number
    success: boolean
    error?: string | null
    normalized_path?: string | null
    normalization_error?: string | null
}
