// Auto-generated – do not edit manually
/** Error message returned by the API, with HTTP status code. */
export interface ErrorHttpCode {
    http_code: number
    message: string
    error?: any | null
}

/** Error message returned by the API */
export interface ErrorMessage {
    message: string
    error?: any | null
}

/** Metadata for one configured service provider. */
export interface ServiceProviderInfo {
    name: string
    service: string
    provider: string
    provider_category: string
    enabled: boolean
    priority: number
}
