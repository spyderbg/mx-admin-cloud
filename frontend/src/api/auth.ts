import client from './client'

interface LoginResponse {
  access_token?: string
  [key: string]: unknown
}

/** Sign-in options the backend allows; read before logging in. */
export interface AuthConfig {
  google_enabled: boolean
  google_client_id?: string | null
}

const storeToken = (data: LoginResponse): LoginResponse => {
  if (data.access_token) {
    localStorage.setItem('token', data.access_token)
  }
  return data
}

export const login = async (username: string, password: string): Promise<LoginResponse> => {
  const params = new URLSearchParams()
  params.append('username', username)
  params.append('password', password)

  const response = await client.post<LoginResponse>('/login', params)
  return storeToken(response.data)
}

export const getAuthConfig = async (): Promise<AuthConfig> => {
  return (await client.get<AuthConfig>('/auth/config')).data
}

/** Exchange the ID token Google returned to the browser for this app's access token. */
export const loginWithGoogle = async (credential: string): Promise<LoginResponse> => {
  const response = await client.post<LoginResponse>('/login/google', { credential })
  return storeToken(response.data)
}

export const getStatus = async (): Promise<unknown> => {
  const token = localStorage.getItem('token')
  const response = await client.get('/status', {
    headers: { Authorization: `Bearer ${token}` }
  })
  return response.data
}

interface FilesResponse {
  directory?: string
  files?: string[]
  error?: string
  [key: string]: unknown
}

/**
 * List the content directory down to `depth` folder levels (1 = top level only).
 * `files` holds paths relative to the content directory; folders end with `/`.
 * The client's interceptor adds the Bearer token.
 */
export const listFiles = async (depth = 1): Promise<FilesResponse> => {
  const response = await client.post<FilesResponse>('/list-files', { depth })
  return response.data
}
