// Auto-generated – do not edit manually
/** Body for single-image generation returning raw bytes. */
export interface GenerateImageBytesRequest {
    prompt: string
    filename_text?: string | null
    provider?: string | null
}

/** Body for a single image generation request. */
export interface GenerateImageRequest {
    prompt: string
    filename_text?: string | null
    negative_prompt?: string | null
    language_code?: string | null
    size?: string | null
    quality?: string | null
    output_format?: string
    seed?: number | null
    request_id?: string | null
    metadata?: Record<string, any>
}

/** Serialized generated-image payload returned by the generate endpoint. */
export interface ImageGenerateResult {
    provider: string
    request_id: string
    prompt: string
    filename_text: string
    output_format: string
    raw_path: string
    raw_extension: string
    latency_seconds: number
    success: boolean
    model?: string | null
    width?: number | null
    height?: number | null
    cost_estimate?: number | null
    attempts?: number
    error?: string | null
}

/** Metadata for one configured image provider (GET /internal/image-generators). */
export interface ImageGeneratorProviderInfo {
    name: string
    provider: string
    enabled: boolean
    priority: number
    config_path: string
    generate: Record<string, any>
    traffic: Record<string, any>
    pricing: Record<string, any>
}
