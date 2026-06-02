import axios from 'axios'

const client = axios.create({ baseURL: '/api/v1' })

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

if (import.meta.env.VITE_VERBOSE_REQUESTS === 'true') {
  client.interceptors.response.use(
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

export default client
