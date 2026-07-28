import { setupWorker } from 'msw/browser'
import { contentHandlers } from './contentHandlers'
import { configurationHandlers } from './configurationHandlers'

export const worker = setupWorker(...contentHandlers, ...configurationHandlers)
