import { ref, shallowRef } from 'vue'
import { listConfigurationServices, listServiceConfigs } from './configurationClient'
import type { ConfigurationFileSummaryResponse } from '../types/configurationSchemas'
import { orderConfigurationFiles, serviceDisplayName } from '../utils/configurationNavigation'

export const configurationServices = shallowRef<string[]>([])
export const configurationServicesLoading = ref(false)
export const configurationServicesError = ref('')
export const serviceConfigurationFiles = shallowRef<Record<string, ConfigurationFileSummaryResponse[]>>({})
export const serviceConfigurationLoading = shallowRef<Record<string, boolean>>({})
export const serviceConfigurationErrors = shallowRef<Record<string, string>>({})

let servicesLoaded = false
let servicesRequest: Promise<string[]> | null = null
const fileRequests = new Map<string, Promise<ConfigurationFileSummaryResponse[]>>()

export function ensureConfigurationServices(): Promise<string[]> {
  if (servicesRequest) return servicesRequest
  if (servicesLoaded) return Promise.resolve(configurationServices.value)
  configurationServicesLoading.value = true
  configurationServicesError.value = ''
  servicesRequest = listConfigurationServices().then(({ services }) => {
    configurationServices.value = [...services].sort((left, right) => serviceDisplayName(left).localeCompare(serviceDisplayName(right)))
    servicesLoaded = true
    return configurationServices.value
  }).catch(error => {
    configurationServicesError.value = 'Could not load configuration services.'
    throw error
  }).finally(() => {
    configurationServicesLoading.value = false
    servicesRequest = null
  })
  return servicesRequest
}

export function ensureServiceConfigurations(service: string): Promise<ConfigurationFileSummaryResponse[]> {
  const pending = fileRequests.get(service)
  if (pending) return pending
  const cached = serviceConfigurationFiles.value[service]
  return cached ? Promise.resolve(cached) : refreshServiceConfigurations(service)
}

export function refreshServiceConfigurations(service: string): Promise<ConfigurationFileSummaryResponse[]> {
  const pending = fileRequests.get(service)
  if (pending) return pending
  serviceConfigurationLoading.value = { ...serviceConfigurationLoading.value, [service]: true }
  serviceConfigurationErrors.value = { ...serviceConfigurationErrors.value, [service]: '' }
  const request = listServiceConfigs(service).then(files => {
    const ordered = orderConfigurationFiles(files)
    serviceConfigurationFiles.value = { ...serviceConfigurationFiles.value, [service]: ordered }
    return ordered
  }).catch(error => {
    serviceConfigurationErrors.value = { ...serviceConfigurationErrors.value, [service]: 'Could not load configurations.' }
    throw error
  }).finally(() => {
    serviceConfigurationLoading.value = { ...serviceConfigurationLoading.value, [service]: false }
    fileRequests.delete(service)
  })
  fileRequests.set(service, request)
  return request
}

export async function ensureConfigurationTree(): Promise<void> {
  const services = await ensureConfigurationServices()
  await Promise.allSettled(services.map(ensureServiceConfigurations))
}
