import { http, HttpResponse } from 'msw'
import type { AddConfigurationRequest, ConfigurationFileSummaryResponse, ProviderSelectionStrategy, ReplaceConfigurationRequest } from '../types/configurationSchemas'

// In-memory fixtures so the configuration popup works with VITE_MOCK_DATA.
const mockConfigs: Record<string, ConfigurationFileSummaryResponse[]> = {
  content: [
    { config_name: 'file_system', filename: 'file_system.json', provider: 'file_system', enabled: true, priority: 1, valid_json: true, error: null, version: null },
  ],
  logging: [
    { config_name: 'default', filename: 'default.json', provider: 'struct_log', enabled: true, priority: 20, valid_json: true, error: null, version: '1.0' },
    { config_name: 'requests', filename: 'requests.json', provider: 'struct_log', enabled: true, priority: 1, valid_json: true, error: null, version: '1.0' },
    { config_name: 'trace', filename: 'trace.json', provider: 'struct_log', enabled: false, priority: 10, valid_json: true, error: null, version: '1.0' },
  ],
  text_to_speech: [
    { config_name: 'elevenlabs', filename: 'elevenlabs.json', provider: 'elevenlabs', enabled: true, priority: 2, valid_json: true, error: null, version: null },
    { config_name: 'openai', filename: 'openai.json', provider: 'openai', enabled: false, priority: 2, valid_json: true, error: null, version: null },
    { config_name: 'speaktor', filename: 'speaktor.json', provider: 'speaktor', enabled: false, priority: 3, valid_json: true, error: null, version: null },
    { config_name: 'static', filename: 'static.json', provider: 'static', enabled: false, priority: 1, valid_json: true, error: null, version: null },
  ],
  text_to_image: [
    { config_name: 'openai', filename: 'openai.json', provider: 'openai', enabled: true, priority: 1, valid_json: true, error: null, version: '1.0' },
    { config_name: 'openai_http', filename: 'openai_http.json', provider: 'openai_http', enabled: false, priority: 2, valid_json: true, error: null, version: '1.0' },
    { config_name: 'genai', filename: 'genai.json', provider: 'genai', enabled: false, priority: 3, valid_json: true, error: null, version: '1.0' },
    { config_name: 'genai_http', filename: 'genai_http.json', provider: 'genai_http', enabled: false, priority: 4, valid_json: true, error: null, version: '1.0' },
    { config_name: 'comfyui', filename: 'comfyui.json', provider: 'comfyui', enabled: false, priority: 5, valid_json: true, error: null, version: '1.0' },
  ],
  text_translation: [
    { config_name: 'static', filename: 'static.json', provider: 'static_translate', enabled: true, priority: 1, valid_json: true, error: null, version: null },
    { config_name: 'openai_en_bg', filename: 'openai_en_bg.json', provider: 'openai_translate', enabled: false, priority: 2, valid_json: true, error: null, version: null },
  ],
  voice_optimization: [
    { config_name: 'ffmpeg_high', filename: 'ffmpeg_high.json', provider: 'ffmpeg', enabled: false, priority: 1, valid_json: true, error: null, version: null },
    { config_name: 'pydub_optimized', filename: 'pydub_optimized.json', provider: 'pydub', enabled: false, priority: 2, valid_json: true, error: null, version: null },
  ],
}

const findSummary = (serviceName: string, configName: string) =>
  mockConfigs[serviceName]?.find((c) => c.config_name === configName)

type JsonObject = Record<string, unknown>
const isObject = (value: unknown): value is JsonObject => value !== null && typeof value === 'object' && !Array.isArray(value)
const sampleSettings: Record<string, Record<string, JsonObject>> = {
  content: { file_system: { content_root: '/app/content', archive_root: '/app/archive' } },
  logging: {
    default: { formatters: { plain: { format: '%(asctime)s %(levelname)s %(message)s' } }, handlers: { console: { class: 'logging.StreamHandler', level: 'INFO' }, file: { class: 'logging.FileHandler', filename: 'app.log' } }, structlog: { renderer: 'console' } },
    requests: { formatters: { plain: { format: '%(message)s' } }, handlers: { requests_file: { class: 'logging.FileHandler', filename: 'requests.log' } }, request_log: { fields: { path: true, duration_ms: true } } },
    trace: { handlers: { console: { class: 'logging.StreamHandler' } }, structlog: { renderer: 'json' } },
  },
  text_to_speech: {
    elevenlabs: { generate: { languages: { en: { model: 'eleven_multilingual_v2', voice_id: 'voice_en' }, es: { model: 'eleven_multilingual_v2', voice_id: 'voice_es' } } }, evaluate: { enabled: true, stt_provider: 'openai', model: 'whisper-1', language: 'en' } },
    openai: { generate: { model: 'gpt-4o-mini-tts', languages: { en: { voice: 'alloy' } } }, evaluate: { enabled: false, stt_provider: 'openai', model: 'whisper-1' } },
    speaktor: { generate: { endpoint: 'https://example.test/tts', method: 'POST', headers: {}, languages: { en: { voice: 'default' } } }, evaluate: { enabled: false, stt_provider: 'openai' } },
    static: { generate: { enabled: true, languages: { en: { directory: '/app/audio/en' } } }, evaluate: { enabled: false } },
  },
  text_to_image: {
    openai: { generate: { model: 'gpt-image-1', size: '1024x1024', quality: 'high', prompt_prefix: '' }, traffic: { max_concurrency: 3, timeout_seconds: 60 }, pricing: { 'gpt-image-1': { high: 0.04 } } },
    openai_http: { generate: { endpoint: 'https://api.openai.com/v1/images/generations', model: 'gpt-image-1', size: '1024x1024' }, traffic: { max_concurrency: 2, timeout_seconds: 60 } },
    genai: { generate: { model: 'imagen-4.0-generate-001', aspect_ratio: '1:1' }, traffic: { max_concurrency: 2 } },
    genai_http: { generate: { model: 'imagen-4.0-fast-generate-001', aspect_ratio: '1:1' }, traffic: { max_concurrency: 2 } },
    comfyui: { generate: { base_url: 'http://localhost:8188', workflow_file: 'default_txt2img.json', steps: 20, seed: null }, traffic: { poll_interval_seconds: 2 } },
  },
  text_translation: {
    static: { input_language: 'en', output_language: 'es', content_dir: '/app/content' },
    openai_en_bg: { input_language: 'en', output_language: 'bg', model: 'gpt-4o-mini', prompt: 'Translate naturally.', temperature: 0 },
  },
  voice_optimization: {
    ffmpeg_high: { sample_rate: 44100, mono: true, vbr_quality: 2 },
    pydub_optimized: { sample_rate: 22050, mono: true, vbr_quality: 4 },
  },
}
const mockPayloads: Record<string, Record<string, JsonObject>> = Object.fromEntries(
  Object.entries(mockConfigs).map(([service, files]) => [service, Object.fromEntries(files.map(file => [file.config_name, {
    service_provider: {
      service, provider: file.provider, enabled: file.enabled, priority: file.priority,
      ...(file.version ? { version: file.version } : {}),
      ...(service === 'logging' && file.config_name === 'requests' ? { provider_category: 'requests' } : {}),
      config: sampleSettings[service]?.[file.config_name] || {},
    },
  }]))]),
)
export function getMockTtsConfiguration(configName: string) {
  const payload = mockPayloads.text_to_speech?.[configName]
  return payload ? { config_name: configName, filename: `${configName}.json`, payload } : undefined
}

const strategies: Record<string, ProviderSelectionStrategy> = { logging: 'ALL_PROVIDERS' }
const supportedStrategies: Record<string, ProviderSelectionStrategy[]> = {
  content: ['FIRST_SUCCESS'], logging: ['ALL_PROVIDERS', 'FIRST_SUCCESS'],
  text_to_speech: ['FIRST_SUCCESS'], text_to_image: ['FIRST_SUCCESS'],
  text_translation: ['FIRST_SUCCESS'], voice_optimization: ['ALL_PROVIDERS', 'FIRST_SUCCESS'],
}
const defaultStrategy = (service: string): ProviderSelectionStrategy | null =>
  !supportedStrategies[service]?.length ? null : service === 'logging' || service === 'voice_optimization' ? 'ALL_PROVIDERS' : 'FIRST_SUCCESS'
const strategyResponse = (service: string) => ({
  service_name: service,
  strategy: strategies[service] || defaultStrategy(service),
  supported_strategies: supportedStrategies[service],
  default_strategy: defaultStrategy(service),
})

function syncSummary(service: string, name: string): void {
  const summary = findSummary(service, name)
  const envelope = mockPayloads[service]?.[name]?.service_provider
  if (!summary || !isObject(envelope)) return
  summary.provider = typeof envelope.provider === 'string' ? envelope.provider : null
  summary.enabled = envelope.enabled === true
  summary.priority = typeof envelope.priority === 'number' ? envelope.priority : null
  summary.version = typeof envelope.version === 'string' ? envelope.version : null
}

function merge(base: JsonObject, patch: JsonObject): JsonObject {
  const result = { ...base }
  for (const [key, value] of Object.entries(patch)) {
    result[key] = isObject(value) && isObject(result[key]) ? merge(result[key], value) : value
  }
  return result
}

const mutationResult = (configName: string, changed = true) =>
  HttpResponse.json({ config_name: configName, changed, error: null })

export const configurationHandlers = [
  // List configurable services
  http.get('/internal/configurations', () =>
    HttpResponse.json({ services: Object.keys(mockConfigs).sort() }),
  ),

  // Apply a runtime configuration payload
  http.post('/internal/configurations/apply', () =>
    HttpResponse.json({ config_name: 'runtime', accepted: true, error: null }),
  ),

  // Upload a runtime configuration file
  http.post('/internal/configurations/upload', () =>
    HttpResponse.json({ config_name: 'runtime', accepted: true, error: null }),
  ),

  // List configuration files for a service
  http.get('/internal/configurations/:service_name', ({ params }) => {
    const summaries = mockConfigs[String(params.service_name)]
    if (!summaries) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json(summaries)
  }),

  http.get('/internal/configurations/:service_name/providers', ({ params }) => {
    const service = String(params.service_name)
    const summaries = mockConfigs[service]
    if (!summaries) return new HttpResponse(null, { status: 404 })
    const providers = [...new Set(summaries.map(file => file.provider).filter((name): name is string => Boolean(name)))].sort()
    return HttpResponse.json({ service_name: service, providers })
  }),

  // Service-wide strategy is stored separately from provider files.
  http.get('/internal/configurations/:service_name/strategy', ({ params }) => {
    const service = String(params.service_name)
    if (!mockConfigs[service]) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json(strategyResponse(service))
  }),
  http.put('/internal/configurations/:service_name/strategy', async ({ params, request }) => {
    const service = String(params.service_name)
    if (!mockConfigs[service]) return new HttpResponse(null, { status: 404 })
    const body = await request.json() as { strategy?: ProviderSelectionStrategy }
    if (!body.strategy || !supportedStrategies[service]?.includes(body.strategy)) return new HttpResponse(null, { status: 422 })
    strategies[service] = body.strategy
    return HttpResponse.json(strategyResponse(service))
  }),

  // Get one configuration file
  http.get('/internal/configurations/:service_name/:config_name', ({ params }) => {
    const service = String(params.service_name)
    const name = String(params.config_name)
    const summary = findSummary(service, name)
    if (!summary) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({
      config_name: summary.config_name,
      filename: summary.filename,
      payload: mockPayloads[service]?.[name],
    })
  }),

  // Create a configuration file for a service
  http.post('/internal/configurations/:service_name', async ({ params, request }) => {
    const service = String(params.service_name)
    if (!mockConfigs[service]) return new HttpResponse(null, { status: 404 })
    const body = await request.json() as AddConfigurationRequest
    const name = body.config_name.replace(/\.json$/i, '')
    if (!/^[A-Za-z0-9._-]+$/.test(name) || findSummary(service, name) || !isObject(body.payload?.service_provider)) return new HttpResponse(null, { status: 422 })
    mockPayloads[service][name] = structuredClone(body.payload)
    mockConfigs[service].push({ config_name: name, filename: `${name}.json`, provider: null, enabled: false, priority: null, version: null, valid_json: true, error: null })
    syncSummary(service, name)
    return HttpResponse.json({ config_name: name, changed: true, error: null }, { status: 201 })
  }),

  // Remove a configuration file from a service
  http.delete('/internal/configurations/:service_name/:config_name', ({ params }) => {
    const service = String(params.service_name)
    const name = String(params.config_name)
    const index = mockConfigs[service]?.findIndex(file => file.config_name === name) ?? -1
    if (index < 0) return new HttpResponse(null, { status: 404 })
    mockConfigs[service].splice(index, 1)
    delete mockPayloads[service][name]
    return mutationResult(name)
  }),

  // Merge-patch a configuration file
  http.patch('/internal/configurations/:service_name/:config_name', async ({ params, request }) => {
    const service = String(params.service_name)
    const name = String(params.config_name)
    const summary = findSummary(service, name)
    if (!summary) return new HttpResponse(null, { status: 404 })
    const body = (await request.json()) as { patch?: JsonObject }
    mockPayloads[service][name] = merge(mockPayloads[service][name], body.patch || {})
    syncSummary(service, name)
    return mutationResult(summary.config_name)
  }),

  http.put('/internal/configurations/:service_name/:config_name', async ({ params, request }) => {
    const service = String(params.service_name)
    const name = String(params.config_name)
    if (!findSummary(service, name)) return new HttpResponse(null, { status: 404 })
    const body = await request.json() as ReplaceConfigurationRequest
    if (!isObject(body.payload?.service_provider)) return new HttpResponse(null, { status: 422 })
    mockPayloads[service][name] = structuredClone(body.payload)
    syncSummary(service, name)
    return mutationResult(name)
  }),

  // Enable selected configuration files for a service
  http.post('/internal/configurations/:service_name/enable-configurations', () => HttpResponse.json({})),

  // Disable selected configuration files for a service
  http.post('/internal/configurations/:service_name/disable-configurations', () => HttpResponse.json({})),

  // Enable every configuration file for a service
  http.post('/internal/configurations/:service_name/enable-all-configurations', () => HttpResponse.json({})),

  // Disable every configuration file for a service
  http.post('/internal/configurations/:service_name/disable-all-configurations', () => HttpResponse.json({})),

]
