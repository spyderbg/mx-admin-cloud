// Auto-generated – do not edit manually
/** Body model for creating a new configuration file. */
export interface AddConfigurationRequest {
    config_name: string
    payload: Record<string, any>
}

/** Body model for direct JSON payload submission. */
export interface ApplyConfigurationRequest {
    payload: Record<string, any>
}

/** Result model for runtime config apply endpoints. */
export interface ApplyConfigurationResponse {
    config_name: string
    accepted: boolean
    error?: string | null
}

/** Body model for enabling/disabling selected config files. */
export interface BatchToggleConfigurationsRequest {
    config_names: string[]
}

/** Result model for batch enable/disable operations. */
export interface BatchToggleConfigurationsResponse {
    service_name: string
    enabled: boolean
    results: ConfigurationMutationResponse[]
}

/** One configuration file entry returned by list operations. */
export interface ConfigurationFileSummaryResponse {
    config_name: string
    filename: string
    provider?: string | null
    enabled?: boolean | null
    priority?: number | null
    valid_json: boolean
    error?: string | null
}

/** Result model for one add/remove/patch mutation. */
export interface ConfigurationMutationResponse {
    config_name: string
    changed: boolean
    error?: string | null
}

/** Body model for merge-patching an existing configuration file. */
export interface PatchConfigurationRequest {
    patch: Record<string, any>
}
