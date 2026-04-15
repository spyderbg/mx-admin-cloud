import { http, HttpResponse } from 'msw'
import { mockCategories } from '../data/mockData'
import type { Category } from '../types'

export const handlers = [
  http.post('/api/v1/login', () => {
    return HttpResponse.json({ access_token: 'debug-token' })
  }),

  http.get('/api/v1/content/categories', () => {
    return HttpResponse.json(mockCategories)
  }),

  http.get('/api/v1/content/categories/:id', ({ params }) => {
    const category = mockCategories.find((c: Category) => c.id === params.id)
    if (!category) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json(category)
  }),
]
