import axios from 'axios'

const API_URL = '/api/v1'

interface LoginResponse {
  access_token?: string
  [key: string]: unknown
}

export const login = async (username: string, password: string): Promise<LoginResponse> => {
  const params = new URLSearchParams()
  params.append('username', username)
  params.append('password', password)

  const response = await axios.post<LoginResponse>(`${API_URL}/login`, params)
  if (response.data.access_token) {
    localStorage.setItem('token', response.data.access_token)
  }
  return response.data
}

export const getStatus = async (): Promise<unknown> => {
  const token = localStorage.getItem('token')
  const response = await axios.get(`${API_URL}/status`, {
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

export const listFiles = async (): Promise<FilesResponse> => {
  const token = localStorage.getItem('token')
  const response = await axios.get<FilesResponse>(`${API_URL}/list`, {
    headers: { Authorization: `Bearer ${token}` }
  })
  return response.data
}
