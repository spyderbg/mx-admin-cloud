// Auto-generated – do not edit manually
/** Provider-independent levels accepted by the internal logging service. */
export type LogLevel = 'trace' | 'debug' | 'info' | 'warning' | 'error' | 'critical' | 'fatal'

/** Body for "POST /internal/loggers/logging/log/api-request". */
export interface LogApiRequest extends LogRequest {
    api_path: string
    api_method: string
    api_status: number
    api_duration: number
}

/** Body for "POST /internal/loggers/emit". */
export interface LogNameRequest extends LogRequest {
    logger_name?: string | null
}

/** Body for "POST /internal/loggers/log/payment". */
export interface LogPaymentRequest extends LogRequest {
    payment_id: string
    payment_status: string
    payment_amount: number
    payment_currency: string
    payment_provider: string
}

/** Body for "POST /internal/loggers/log/provider".

    The trailing fields are optional extras used by image generation
    ("request_kind='image'"); omitting them keeps TTS/translation records
    byte-identical because "to_request_emit_fields()" excludes "None". */
export interface LogProviderRequest {
    provider?: string | null
    model?: string | null
    actor?: string | null
    voice_id?: string | null
    language?: string | null
    input?: any | null
    response_duration?: number | null
    request_kind?: string | null
    size?: string | null
    quality?: string | null
    cost_estimate?: number | null
    status?: string | null
    error?: string | null
    attempts?: number | null
}

/** Base body for "POST /internal/loggers/emit" and "POST /internal/loggers/log/api-request". */
export interface LogRequest {
    level?: LogLevel
    event: string
    fields?: Record<string, any>
}

/** Body for "POST /internal/loggers/log/service". */
export interface LogServiceRequest extends LogRequest {
    service: string
}

/** Acknowledgement payload for reset endpoint with affected files. */
export interface ResetLogsResponse extends StatusResponse {
    reset_files?: string[]
}

/** Generic acknowledgement returned by fire-and-forget logging endpoints. */
export interface StatusResponse {
    status: string
}
