import { setupWorker } from 'msw/browser'
import { contentHandlers } from './contentHandlers'
import { configurationHandlers } from './configurationHandlers'
import { itemVoiceHandlers } from './itemVoiceHandlers'

export const worker = setupWorker(...itemVoiceHandlers, ...contentHandlers, ...configurationHandlers)
