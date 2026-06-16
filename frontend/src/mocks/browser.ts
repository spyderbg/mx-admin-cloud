import { setupWorker } from 'msw/browser'
import { contentHandlers } from './contentHandlers'

export const worker = setupWorker(...contentHandlers)
