import type { ConfigurationFileSummaryResponse } from '../types/configurationSchemas'

export const serviceDisplayName = (service: string): string =>
  service.split('_').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' ')

export function orderConfigurationFiles(files: ConfigurationFileSummaryResponse[]): ConfigurationFileSummaryResponse[] {
  return [...files].sort((left, right) => {
    const leftPriority = left.priority ?? Number.POSITIVE_INFINITY
    const rightPriority = right.priority ?? Number.POSITIVE_INFINITY
    return leftPriority === rightPriority
      ? left.filename.localeCompare(right.filename)
      : leftPriority - rightPriority
  })
}
