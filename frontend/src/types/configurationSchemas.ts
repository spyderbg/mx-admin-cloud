// Auto-generated – do not edit manually
/** Planned provider dispatch policy for a service. */
export type ProviderSelectionStrategy = 'ALL_PROVIDERS' | 'FIRST_SUCCESS'

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

/** Full JSON payload of one configuration file. */
export interface ConfigurationFileResponse {
    config_name: string
    filename: string
    payload: Record<string, any>
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
    version?: string | null
}

/** Result model for one add/remove/patch mutation. */
export interface ConfigurationMutationResponse {
    config_name: string
    changed: boolean
    error?: string | null
}

/** Provider types available to one configurable service. */
export interface ConfigurationProvidersResponse {
    service_name: string
    providers: string[]
}

/** Names of services that accept configuration files. */
export interface ConfigurationServicesResponse {
    services: string[]
}

/** Body model for merge-patching an existing configuration file. */
export interface PatchConfigurationRequest {
    patch: Record<string, any>
}

/** Selection strategy to persist for a service. */
export interface ProviderStrategyRequest {
    strategy: ProviderSelectionStrategy
}

/** Effective strategy and available choices for a service. */
export interface ProviderStrategyResponse {
    service_name: string
    strategy: ProviderSelectionStrategy | null
    supported_strategies: ProviderSelectionStrategy[]
    default_strategy: ProviderSelectionStrategy | null
}

/** Body model for replacing the complete JSON payload of one file. */
export interface ReplaceConfigurationRequest {
    payload: Record<string, any>
}
