import axios from 'axios'
import type { AxiosInstance } from 'axios'

/** Create an axios instance with the Bearer-token and logging interceptors attached. */
export function createApiClient(baseURL: string): AxiosInstance {
  const instance = axios.create({ baseURL })

  instance.interceptors.request.use((config) => {
    const token = localStorage.getItem('token')
    if (token) {
      config.headers = config.headers ?? {}
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  })

  if (import.meta.env.VITE_LOG_REQUESTS === 'true') {
    instance.interceptors.request.use((config) => {
      const method = config.method?.toUpperCase() ?? 'GET'
      const url = config.url ?? ''
      const params = config.params ? ` params=${JSON.stringify(config.params)}` : ''
      const body = config.data ? ` body=${JSON.stringify(config.data)}` : ''
      console.log(`[REQUEST] ${method} ${url}${params}${body}`)
      return config
    })
  }

  if (import.meta.env.VITE_VERBOSE_REQUESTS === 'true') {
    instance.interceptors.response.use(
      (response) => {
        const method = response.config.method?.toUpperCase() ?? 'GET'
        const url = response.config.url ?? ''
        const status = response.status
        const data = response.data
        console.log(`[VERBOSE] ${method} ${url} → ${status}`, JSON.stringify(data, null, 2))
        return response
      },
      (error) => {
        const method = error.config?.method?.toUpperCase() ?? 'UNKNOWN'
        const url = error.config?.url ?? ''
        const status = error.response?.status ?? 'N/A'
        const data = error.response?.data
        console.error(`[VERBOSE] ${method} ${url} ✗ ${status}`, data ? JSON.stringify(data, null, 2) : '')
        return Promise.reject(error)
      },
    )
  }

  return instance
}

const client = createApiClient('/api/v1')

export default client
