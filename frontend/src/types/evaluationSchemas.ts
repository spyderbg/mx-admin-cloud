// Auto-generated – do not edit manually
/** Body for a single evaluation request. */
export interface EvaluateRequest {
    provider: string
    request_id: string
    expected_text: string
    language_code: string
    language_name: string
    filename_text: string
    output_format: string
    raw_path: string
    raw_extension: string
    latency_seconds: number
    normalized_path?: string | null
    normalization_error?: string | null
    evaluate_config: Record<string, any>
    output_dir: string
}
