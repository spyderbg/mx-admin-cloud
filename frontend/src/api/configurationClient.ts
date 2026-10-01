// Auto-generated – do not edit manually

import client from './internalClient'
import type {
  AddConfigurationRequest,
  ApplyConfigurationRequest,
  ApplyConfigurationResponse,
  BatchToggleConfigurationsRequest,
  BatchToggleConfigurationsResponse,
  ConfigurationFileResponse,
  ConfigurationFileSummaryResponse,
  ConfigurationMutationResponse,
  ConfigurationServicesResponse,
  PatchConfigurationRequest,
  ProviderStrategyRequest,
  ProviderStrategyResponse,
  ReplaceConfigurationRequest,
} from '../types/configurationSchemas'

/** List configurable services */
export const listConfigurationServices = async (): Promise<ConfigurationServicesResponse> => {
  const r = await client.get<ConfigurationServicesResponse>('/internal/configurations')
  return r.data
}

/** Apply a runtime configuration payload */
export const applyConfigPayload = async (body: ApplyConfigurationRequest): Promise<ApplyConfigurationResponse> => {
  const r = await client.post<ApplyConfigurationResponse>('/internal/configurations/apply', body)
  return r.data
}

/** Upload a runtime configuration file */
export const uploadConfigFile = async (formData: FormData): Promise<ApplyConfigurationResponse> => {
  const r = await client.post<ApplyConfigurationResponse>('/internal/configurations/upload', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  return r.data
}

/** List configuration files for a service */
export const listServiceConfigs = async (serviceName: string): Promise<ConfigurationFileSummaryResponse[]> => {
  const r = await client.get<ConfigurationFileSummaryResponse[]>(`/internal/configurations/${serviceName}`)
  return r.data
}

/** Get provider selection strategy for a service */
export const getProviderStrategy = async (serviceName: string): Promise<ProviderStrategyResponse> => {
  const r = await client.get<ProviderStrategyResponse>(`/internal/configurations/${serviceName}/strategy`)
  return r.data
}

/** Save provider selection strategy for a service */
export const setProviderStrategy = async (serviceName: string, body: ProviderStrategyRequest): Promise<ProviderStrategyResponse> => {
  const r = await client.put<ProviderStrategyResponse>(`/internal/configurations/${serviceName}/strategy`, body)
  return r.data
}

/** Get one configuration file */
export const getServiceConfig = async (serviceName: string, configName: string): Promise<ConfigurationFileResponse> => {
  const r = await client.get<ConfigurationFileResponse>(`/internal/configurations/${serviceName}/${configName}`)
  return r.data
}

/** Create a configuration file for a service */
export const addServiceConfig = async (serviceName: string, body: AddConfigurationRequest): Promise<ConfigurationMutationResponse> => {
  const r = await client.post<ConfigurationMutationResponse>(`/internal/configurations/${serviceName}`, body)
  return r.data
}

/** Remove a configuration file from a service */
export const removeServiceConfig = async (serviceName: string, configName: string): Promise<ConfigurationMutationResponse> => {
  const r = await client.delete<ConfigurationMutationResponse>(`/internal/configurations/${serviceName}/${configName}`)
  return r.data
}

/** Merge-patch a configuration file */
export const patchServiceConfig = async (serviceName: string, configName: string, body: PatchConfigurationRequest): Promise<ConfigurationMutationResponse> => {
  const r = await client.patch<ConfigurationMutationResponse>(`/internal/configurations/${serviceName}/${configName}`, body)
  return r.data
}

/** Replace a configuration file */
export const replaceServiceConfig = async (serviceName: string, configName: string, body: ReplaceConfigurationRequest): Promise<ConfigurationMutationResponse> => {
  const r = await client.put<ConfigurationMutationResponse>(`/internal/configurations/${serviceName}/${configName}`, body)
  return r.data
}

/** Enable selected configuration files for a service */
export const enableConfigurations = async (serviceName: string, body: BatchToggleConfigurationsRequest): Promise<BatchToggleConfigurationsResponse> => {
  const r = await client.post<BatchToggleConfigurationsResponse>(`/internal/configurations/${serviceName}/enable-configurations`, body)
  return r.data
}

/** Disable selected configuration files for a service */
export const disableConfigurations = async (serviceName: string, body: BatchToggleConfigurationsRequest): Promise<BatchToggleConfigurationsResponse> => {
  const r = await client.post<BatchToggleConfigurationsResponse>(`/internal/configurations/${serviceName}/disable-configurations`, body)
  return r.data
}

/** Enable every configuration file for a service */
export const enableAllConfigurations = async (serviceName: string): Promise<BatchToggleConfigurationsResponse> => {
  const r = await client.post<BatchToggleConfigurationsResponse>(`/internal/configurations/${serviceName}/enable-all-configurations`)
  return r.data
}

/** Disable every configuration file for a service */
export const disableAllConfigurations = async (serviceName: string): Promise<BatchToggleConfigurationsResponse> => {
  const r = await client.post<BatchToggleConfigurationsResponse>(`/internal/configurations/${serviceName}/disable-all-configurations`)
  return r.data
}
