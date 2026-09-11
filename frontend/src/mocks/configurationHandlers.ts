import { http, HttpResponse } from 'msw'
import type { ConfigurationFileSummaryResponse } from '../types/configurationSchemas'

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

  // Get one configuration file
  http.get('/internal/configurations/:service_name/:config_name', ({ params }) => {
    const summary = findSummary(String(params.service_name), String(params.config_name))
    if (!summary) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({
      config_name: summary.config_name,
      filename: summary.filename,
      payload: {
        service_provider: {
          service: params.service_name,
          provider: summary.provider,
          enabled: summary.enabled,
          priority: summary.priority,
          ...(summary.version ? { version: summary.version } : {}),
          config: {},
        },
      },
    })
  }),

  // Create a configuration file for a service
  http.post('/internal/configurations/:service_name', () => HttpResponse.json({}, { status: 201 })),

  // Remove a configuration file from a service
  http.delete('/internal/configurations/:service_name/:config_name', ({ params }) =>
    mutationResult(String(params.config_name)),
  ),

  // Merge-patch a configuration file
  http.patch('/internal/configurations/:service_name/:config_name', async ({ params, request }) => {
    const summary = findSummary(String(params.service_name), String(params.config_name))
    if (!summary) return new HttpResponse(null, { status: 404 })
    const body = (await request.json()) as { patch?: { service_provider?: Record<string, unknown> } }
    const fields = body?.patch?.service_provider ?? {}
    if (typeof fields.enabled === 'boolean') summary.enabled = fields.enabled
    if (typeof fields.priority === 'number') summary.priority = fields.priority
    if (typeof fields.version === 'string') summary.version = fields.version || null
    return mutationResult(summary.config_name)
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
