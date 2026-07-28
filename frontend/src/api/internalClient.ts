import { createApiClient } from './client'

// Internal routers (/internal/...) are mounted at the server root, outside /api/v1.
const internalClient = createApiClient('')

export default internalClient
