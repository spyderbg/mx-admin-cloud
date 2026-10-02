<template>
  <div class="service-config-page">
    <nav class="cfg-breadcrumb" aria-label="Breadcrumb">
      <span>Configuration</span><i class="mdi mdi-chevron-right" aria-hidden="true"></i><strong>{{ serviceLabel }}</strong>
    </nav>
    <div class="cfg-page-heading">
      <div>
        <h1>{{ serviceLabel }} Configuration</h1>
        <p>Manage provider files and their settings for this service.</p>
      </div>
      <button class="btn btn-primary cfg-add-top" type="button" :disabled="loading || busy" @click="openCreate(false)">
        <i class="mdi mdi-plus" aria-hidden="true"></i> Add configuration
      </button>
    </div>

    <div v-if="pageError" class="alert alert-danger" role="alert">{{ pageError }}</div>

    <div class="cfg-strategy-section-heading">
      <h2 id="strategy-title">Provider selection strategy</h2>
      <span v-if="strategySaving" class="cfg-saving-label">Saving…</span>
    </div>
    <section class="cfg-strategy-card" aria-labelledby="strategy-title">
      <div class="cfg-strategy-options">
        <label class="cfg-strategy-option" :class="{ selected: strategy === 'FIRST_SUCCESS', unsupported: !supportedStrategies.includes('FIRST_SUCCESS') }">
          <input type="radio" name="provider-strategy" value="FIRST_SUCCESS" :checked="strategy === 'FIRST_SUCCESS'" :disabled="strategyLoading || strategySaving || loading || !supportedStrategies.includes('FIRST_SUCCESS')" @change="changeStrategy('FIRST_SUCCESS')" />
          <span><strong>FIRST_SUCCESS</strong><small>Try active providers in priority order until one succeeds.</small></span>
        </label>
        <label class="cfg-strategy-option" :class="{ selected: strategy === 'ALL_PROVIDERS', unsupported: !supportedStrategies.includes('ALL_PROVIDERS') }">
          <input type="radio" name="provider-strategy" value="ALL_PROVIDERS" :checked="strategy === 'ALL_PROVIDERS'" :disabled="strategyLoading || strategySaving || loading || !supportedStrategies.includes('ALL_PROVIDERS')" @change="changeStrategy('ALL_PROVIDERS')" />
          <span><strong>ALL_PROVIDERS</strong><small>Call every active provider for each task, such as logging.</small></span>
        </label>
      </div>
      <p v-if="!supportedStrategies.length" class="cfg-strategy-footnote">This service does not currently use a configurable provider strategy.</p>
      <p v-else-if="supportedStrategies.length === 1" class="cfg-strategy-footnote">Additional strategies will become available as this service supports them.</p>
      <p v-else class="cfg-strategy-footnote">Your selection is saved for this service.</p>
      <p v-if="strategyError" class="text-danger cfg-inline-error" role="alert">{{ strategyError }}</p>
    </section>

    <section class="cfg-configurations" aria-labelledby="config-list-title">
      <div class="cfg-configurations-heading">
        <div>
          <h2 id="config-list-title">Configurations <span>{{ summaries.length }}</span></h2>
          <p>Quick fields save when changed. Expand a card for provider-specific settings.</p>
        </div>
        <button type="button" class="btn btn-primary btn-sm cfg-add-inline" :disabled="loading || busy" @click="openCreate(false)"><i class="mdi mdi-plus"></i> Add</button>
      </div>
      <div v-if="loading" class="cfg-list-message">Loading configurations…</div>
      <div v-else-if="summaries.length === 0" class="cfg-list-message">No configuration files yet. Add one to get started.</div>
      <div v-else class="cfg-card-list">
          <article v-for="summary in orderedSummaries" :key="summary.config_name" class="cfg-config-card" :class="{ expanded: selectedName === summary.config_name }">
            <div class="cfg-card-header">
              <button type="button" class="cfg-card-more" :aria-label="`${selectedName === summary.config_name ? 'Collapse' : 'Expand'} ${summary.filename}`" :aria-expanded="selectedName === summary.config_name" :disabled="busy || !summary.valid_json" @click="selectConfig(summary.config_name)"><i class="mdi" :class="selectedName === summary.config_name ? 'mdi-chevron-up' : 'mdi-chevron-down'" aria-hidden="true"></i></button>
              <span class="cfg-card-icon"><i class="mdi mdi-file-document-outline" aria-hidden="true"></i></span>
              <div class="cfg-card-identity">
                <h3>{{ summary.filename }}</h3>
                <div class="cfg-card-meta">
                  <button v-if="summary.valid_json" type="button" class="cfg-status-pill" :class="summary.enabled ? 'active' : 'inactive'" :aria-label="`${summary.enabled ? 'Disable' : 'Enable'} ${summary.filename}`" :aria-pressed="summary.enabled === true" :disabled="busy" @click="toggleCardEnabled(summary)">{{ summary.enabled ? 'Active' : 'Inactive' }}</button>
                  <span v-else class="cfg-status-pill invalid">Invalid</span>
                  <span v-if="summary.priority !== null && summary.priority !== undefined" class="cfg-priority-pill">Priority {{ summary.priority }}</span>
                </div>
              </div>
              <div class="cfg-card-controls">
                <div class="cfg-card-actions" role="group" :aria-label="`${summary.filename} actions`">
                  <button type="button" class="cfg-card-action-duplicate" :aria-label="`Duplicate ${summary.filename}`" title="Duplicate" :disabled="busy || !summary.valid_json" @click="openCreate(true, summary.config_name)"><i class="mdi mdi-content-copy" aria-hidden="true"></i></button>
                  <button type="button" class="cfg-card-action-remove" :aria-label="`Remove ${summary.filename}`" title="Remove" :disabled="busy" @click="removeConfiguration(summary.config_name)"><i class="mdi mdi-delete-outline" aria-hidden="true"></i></button>
                </div>
              </div>
            </div>
            <div v-if="summary.valid_json" class="cfg-card-quick-fields">
              <div class="cfg-quick-field"><label :for="`cfg-service-${summary.config_name}`">Service</label><input :id="`cfg-service-${summary.config_name}`" class="form-control" type="text" :value="serviceName" readonly title="A configuration belongs to the service shown on this page" /></div>
              <div class="cfg-quick-field"><label :for="`cfg-provider-${summary.config_name}`">Provider</label><select :id="`cfg-provider-${summary.config_name}`" class="form-control" :value="quickValue(summary, 'provider')" :disabled="busy || !cardFiles[summary.config_name]" @change="saveQuickField(summary, 'provider', $event)"><option value="" disabled>Select provider</option><option v-for="provider in providerChoices" :key="provider" :value="provider">{{ provider }}</option></select></div>
              <div class="cfg-quick-field"><label :for="`cfg-category-${summary.config_name}`">Provider category</label><input :id="`cfg-category-${summary.config_name}`" class="form-control" type="text" :value="quickValue(summary, 'provider_category')" placeholder="Optional" :disabled="busy || !cardFiles[summary.config_name]" @change="saveQuickField(summary, 'provider_category', $event)" /></div>
              <div class="cfg-quick-field"><label :for="`cfg-priority-${summary.config_name}`">Priority</label><input :id="`cfg-priority-${summary.config_name}`" class="form-control" type="number" step="1" :value="quickValue(summary, 'priority')" :disabled="busy || !cardFiles[summary.config_name]" @change="saveQuickField(summary, 'priority', $event)" /></div>
              <div class="cfg-quick-field"><label :for="`cfg-version-${summary.config_name}`">Version</label><input :id="`cfg-version-${summary.config_name}`" class="form-control" type="text" :value="quickValue(summary, 'version')" placeholder="Optional" :disabled="busy || !cardFiles[summary.config_name]" @change="saveQuickField(summary, 'version', $event)" /></div>
            </div>
            <p v-else class="cfg-card-invalid">This file is not valid JSON. Remove it or repair it outside the configuration editor.</p>
            <section v-if="selectedName === summary.config_name" id="cfg-expanded-editor" class="cfg-expanded-editor" :aria-label="`Configuration details for ${summary.filename}`">
            <div v-if="detailLoading" class="cfg-panel-message">Loading file…</div>
            <div v-else-if="detailError" class="cfg-panel-message text-danger" role="alert">{{ detailError }}</div>
            <template v-else-if="draft && currentFile">
              <div class="cfg-tabs" role="tablist" aria-label="Editor mode">
                <button type="button" role="tab" :aria-selected="editorMode === 'form'" :class="{ active: editorMode === 'form' }" @click="switchEditor('form')">Form</button>
                <button type="button" role="tab" :aria-selected="editorMode === 'json'" :class="{ active: editorMode === 'json' }" @click="switchEditor('json')">JSON</button>
              </div>
              <div v-if="editorMode === 'form'" class="cfg-editor-content">
                <section class="cfg-form-section">
                  <h4>Provider settings</h4>
                  <p class="cfg-section-help">Edit nested settings and add or remove fields as needed.</p>
                  <DynamicConfigFields :key="`fields-${selectedName}`" :model-value="providerConfig" @update:model-value="updateEnvelope('config', $event)" @validity="setValidity" />
                </section>
                <p v-if="Object.keys(rootExtras).length" class="cfg-section-help">Additional top-level JSON data is available in the JSON tab.</p>
              </div>
              <div v-else class="cfg-editor-content">
                <p class="cfg-section-help">Edit the complete file. The saved JSON replaces the existing payload.</p>
                <textarea v-model="jsonText" class="form-control cfg-full-json" :class="{ 'is-invalid': jsonError }" spellcheck="false" aria-label="Complete configuration JSON" @input="validateJson"></textarea>
                <p v-if="jsonError" class="text-danger cfg-inline-error" role="alert">{{ jsonError }}</p>
              </div>
              <div class="cfg-editor-footer">
                <span v-if="invalidFields.size" class="text-danger">Correct invalid fields before saving.</span>
                <span v-else-if="hasChanges" class="cfg-unsaved">Unsaved changes</span>
                <span v-else class="cfg-saved">All changes saved</span>
                <div class="cfg-footer-actions"><button type="button" class="btn btn-secondary" :disabled="busy" @click="closeEditor">Cancel</button><button type="button" class="btn btn-primary" :disabled="!hasChanges || busy || Boolean(jsonError) || invalidFields.size > 0" @click="saveCurrent">{{ busy ? 'Saving…' : 'Save changes' }}</button></div>
              </div>
            </template>
            </section>
          </article>
      </div>
    </section>

    <Teleport to="body">
      <div v-if="createOpen" class="cfg-dialog-backdrop" @click.self="closeCreate">
        <div class="cfg-dialog" role="dialog" aria-modal="true" aria-labelledby="cfg-create-title">
          <div class="cfg-dialog-header"><h2 id="cfg-create-title">{{ createIsDuplicate ? 'Duplicate configuration' : 'Add configuration' }}</h2><button type="button" aria-label="Close" @click="closeCreate">×</button></div>
          <div class="cfg-dialog-body">
            <label for="cfg-new-name">File name</label>
            <div class="cfg-name-input"><input id="cfg-new-name" v-model="createName" class="form-control" type="text" placeholder="e.g. openai_backup" /><span>.json</span></div>
            <small>Letters, numbers, dots, dashes, and underscores.</small>
            <label for="cfg-template">Start from</label>
            <select id="cfg-template" v-model="createTemplate" class="form-control" :disabled="createBusy" @change="changeTemplate">
              <option value="">Blank configuration</option>
              <option v-for="summary in orderedSummaries.filter(row => row.valid_json)" :key="summary.config_name" :value="summary.config_name">{{ summary.filename }}</option>
            </select>
            <small v-if="createTemplate">Copies the selected file with Enabled set to false.</small>
            <label for="cfg-create-json">Configuration JSON</label>
            <textarea id="cfg-create-json" v-model="createJson" class="form-control cfg-create-json" spellcheck="false"></textarea>
            <p v-if="createError" class="text-danger cfg-inline-error" role="alert">{{ createError }}</p>
          </div>
          <div class="cfg-dialog-footer"><button type="button" class="btn btn-secondary" :disabled="createBusy" @click="closeCreate">Cancel</button><button type="button" class="btn btn-primary" :disabled="createBusy" @click="createConfiguration">{{ createBusy ? 'Creating…' : 'Create configuration' }}</button></div>
        </div>
      </div>
    </Teleport>
    <Teleport to="body">
      <div v-if="toast" class="cd-toast cd-toast-success" role="status">
        <i class="mdi mdi-check-circle" aria-hidden="true"></i>
        <span>{{ toast }}</span>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { AxiosError } from 'axios'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import DynamicConfigFields from '../components/DynamicConfigFields.vue'
import { addServiceConfig, getProviderStrategy, getServiceConfig, listServiceConfigs, listServiceProviders, removeServiceConfig, replaceServiceConfig, setProviderStrategy } from '../api/configurationClient'
import type { ConfigurationFileResponse, ConfigurationFileSummaryResponse, ProviderSelectionStrategy } from '../types/configurationSchemas'

type JsonObject = Record<string, unknown>
const route = useRoute()
const serviceName = computed(() => String(route.params.serviceName || ''))
const serviceLabel = computed(() => serviceName.value.split('_').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' '))
const summaries = ref<ConfigurationFileSummaryResponse[]>([])
const availableProviders = ref<string[]>([])
const cardFiles = ref<Record<string, ConfigurationFileResponse>>({})
const selectedName = ref<string | null>(null)
const currentFile = ref<ConfigurationFileResponse | null>(null)
const draft = ref<JsonObject | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const busy = ref(false)
const pageError = ref('')
const detailError = ref('')
const toast = ref<string | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | null = null
const strategy = ref<ProviderSelectionStrategy | null>(null)
const supportedStrategies = ref<ProviderSelectionStrategy[]>([])
const strategyLoading = ref(false)
const strategySaving = ref(false)
const strategyError = ref('')
const editorMode = ref<'form' | 'json'>('form')
const jsonText = ref('')
const jsonError = ref('')
const invalidFields = ref(new Set<string>())
const createOpen = ref(false)
const createIsDuplicate = ref(false)
const createBusy = ref(false)
const createName = ref('')
const createTemplate = ref('')
const createJson = ref('')
const createError = ref('')
let pageRequest = 0
let detailRequest = 0

const isObject = (value: unknown): value is JsonObject => value !== null && typeof value === 'object' && !Array.isArray(value)
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const envelope = computed<JsonObject>(() => isObject(draft.value?.service_provider) ? draft.value.service_provider : {})
const providerConfig = computed<JsonObject>(() => isObject(envelope.value.config) ? envelope.value.config : {})
const rootExtras = computed<JsonObject>(() => Object.fromEntries(Object.entries(draft.value || {}).filter(([key]) => key !== 'service_provider')))
const providerChoices = computed(() => [...new Set([
  ...availableProviders.value,
  ...summaries.value.map(row => row.provider).filter((value): value is string => typeof value === 'string' && Boolean(value)),
])].sort())
const orderedSummaries = computed(() => [...summaries.value].sort((left, right) => {
  const leftPriority = left.priority ?? Number.POSITIVE_INFINITY
  const rightPriority = right.priority ?? Number.POSITIVE_INFINITY
  return leftPriority === rightPriority
    ? left.filename.localeCompare(right.filename)
    : leftPriority - rightPriority
}))
const hasChanges = computed(() => {
  if (!currentFile.value || !draft.value) return false
  if (editorMode.value === 'json') {
    const candidate = parseJsonObject(jsonText.value)
    return candidate === null
      ? jsonText.value !== JSON.stringify(draft.value, null, 2)
      : JSON.stringify(candidate) !== JSON.stringify(currentFile.value.payload)
  }
  return JSON.stringify(draft.value) !== JSON.stringify(currentFile.value.payload)
})

function message(error: unknown): string {
  const detail = (error as AxiosError<{ detail?: string }>).response?.data?.detail
  return typeof detail === 'string' ? detail : error instanceof Error ? error.message : 'The operation failed. Please try again.'
}

function clearToast(): void {
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = null
  toast.value = null
}

function showToast(text: string): void {
  clearToast()
  toast.value = text
  toastTimer = setTimeout(() => { toast.value = null; toastTimer = null }, 4000)
}

function parseJsonObject(text: string): JsonObject | null {
  try { const value: unknown = JSON.parse(text); return isObject(value) ? value : null } catch { return null }
}

async function loadPage(): Promise<void> {
  const request = ++pageRequest
  ++detailRequest
  loading.value = true
  strategyLoading.value = true
  pageError.value = ''
  detailError.value = ''
  clearToast()
  summaries.value = []
  availableProviders.value = []
  cardFiles.value = {}
  selectedName.value = null
  strategy.value = null
  supportedStrategies.value = []
  currentFile.value = null
  draft.value = null
  try {
    const [files, strategyResponse, providersResponse] = await Promise.all([
      listServiceConfigs(serviceName.value),
      getProviderStrategy(serviceName.value),
      listServiceProviders(serviceName.value).catch(() => null),
    ])
    if (request !== pageRequest) return
    summaries.value = files
    availableProviders.value = providersResponse?.providers ?? []
    const details = await Promise.allSettled(files.filter(row => row.valid_json).map(row => getServiceConfig(serviceName.value, row.config_name)))
    if (request !== pageRequest) return
    cardFiles.value = Object.fromEntries(details.flatMap(result => result.status === 'fulfilled' ? [[result.value.config_name, result.value]] : []))
    strategy.value = strategyResponse.strategy
    supportedStrategies.value = strategyResponse.supported_strategies
    const preferred = typeof route.query.config === 'string' ? route.query.config : ''
    const requested = files.find(row => row.config_name === preferred)
    if (requested) await loadConfig(requested.config_name)
  } catch (error) {
    if (request === pageRequest) pageError.value = message(error)
  } finally {
    if (request === pageRequest) { loading.value = false; strategyLoading.value = false }
  }
}

async function loadConfig(name: string): Promise<void> {
  const request = ++detailRequest
  selectedName.value = name
  currentFile.value = null
  draft.value = null
  detailError.value = ''
  detailLoading.value = true
  editorMode.value = 'form'
  invalidFields.value = new Set()
  try {
    const file = await getServiceConfig(serviceName.value, name)
    if (request !== detailRequest) return
    currentFile.value = file
    cardFiles.value = { ...cardFiles.value, [name]: file }
    draft.value = clone(file.payload)
  } catch (error) {
    if (request === detailRequest) detailError.value = message(error)
  } finally {
    if (request === detailRequest) detailLoading.value = false
  }
}

function confirmDiscard(): boolean {
  return (!hasChanges.value && invalidFields.value.size === 0) || window.confirm('Discard unsaved changes to this configuration?')
}

function clearSelection(): void {
  ++detailRequest
  selectedName.value = null
  currentFile.value = null
  draft.value = null
  detailLoading.value = false
  invalidFields.value = new Set()
}

function closeEditor(): void {
  if (confirmDiscard()) clearSelection()
}

async function selectConfig(name: string): Promise<void> {
  if (busy.value || !confirmDiscard()) return
  if (name === selectedName.value) { clearSelection(); return }
  await loadConfig(name)
  if (selectedName.value === name && window.matchMedia('(max-width: 700px)').matches) {
    await nextTick()
    document.getElementById('cfg-expanded-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

type QuickField = 'provider' | 'provider_category' | 'priority' | 'version' | 'enabled'

function quickValue(summary: ConfigurationFileSummaryResponse, key: Exclude<QuickField, 'enabled'>): string {
  const file = cardFiles.value[summary.config_name]
  const provider = isObject(file?.payload.service_provider) ? file.payload.service_provider : {}
  const value = provider[key]
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return ''
}

async function saveQuickField(summary: ConfigurationFileSummaryResponse, key: Exclude<QuickField, 'enabled'>, event: Event): Promise<void> {
  const input = event.target as HTMLInputElement | HTMLSelectElement
  const previous = quickValue(summary, key)
  const value = input.value.trim()
  if (value === previous) return
  if (key === 'provider' && !value) { pageError.value = 'Provider is required.'; input.value = previous; return }
  if (key === 'priority' && (!value || !Number.isInteger(Number(value)))) { pageError.value = 'Priority must be a whole number.'; input.value = previous; return }
  if (selectedName.value === summary.config_name && !confirmDiscard()) { input.value = previous; return }
  await persistQuickField(summary, key, key === 'priority' ? Number(value) : value, input, previous)
}

async function persistQuickField(summary: ConfigurationFileSummaryResponse, key: QuickField, value: string | number | boolean, input?: HTMLInputElement | HTMLSelectElement, previous?: string): Promise<void> {
  if (busy.value || !summary.valid_json) return
  busy.value = true
  pageError.value = ''
  try {
    const file = await getServiceConfig(serviceName.value, summary.config_name)
    const payload = clone(file.payload)
    if (!isObject(payload.service_provider)) throw new Error('This file has no service_provider object.')
    if ((key === 'version' || key === 'provider_category') && value === '') delete payload.service_provider[key]
    else payload.service_provider[key] = value
    await replaceServiceConfig(serviceName.value, summary.config_name, { payload })
    const [rows, saved] = await Promise.all([listServiceConfigs(serviceName.value), getServiceConfig(serviceName.value, summary.config_name)])
    summaries.value = rows
    cardFiles.value = { ...cardFiles.value, [summary.config_name]: saved }
    if (selectedName.value === summary.config_name) await loadConfig(summary.config_name)
    showToast(`${summary.filename} saved.`)
  } catch (error) {
    pageError.value = message(error)
    if (input && previous !== undefined) input.value = previous
  } finally { busy.value = false }
}

async function toggleCardEnabled(summary: ConfigurationFileSummaryResponse): Promise<void> {
  if (busy.value || !summary.valid_json) return
  if (selectedName.value === summary.config_name && !confirmDiscard()) return
  await persistQuickField(summary, 'enabled', !summary.enabled)
}

async function changeStrategy(next: ProviderSelectionStrategy): Promise<void> {
  if (next === strategy.value || strategySaving.value || !supportedStrategies.value.includes(next)) return
  const service = serviceName.value
  strategySaving.value = true
  strategyError.value = ''
  try {
    const result = await setProviderStrategy(service, { strategy: next })
    if (service !== serviceName.value) return
    strategy.value = result.strategy
    supportedStrategies.value = result.supported_strategies
    showToast('Provider strategy saved.')
  } catch (error) {
    if (service === serviceName.value) strategyError.value = message(error)
  } finally { strategySaving.value = false }
}

function updateEnvelope(key: string, value: unknown): void {
  if (!draft.value) return
  draft.value = { ...draft.value, service_provider: { ...envelope.value, [key]: value } }
}

function setValidity(path: string, valid: boolean): void {
  const next = new Set(invalidFields.value)
  if (valid) {
    for (const field of next) if (field === path || field.startsWith(`${path}.`)) next.delete(field)
  } else next.add(path)
  invalidFields.value = next
}

function validateJson(): void {
  jsonError.value = parseJsonObject(jsonText.value) ? '' : 'Enter a valid JSON object.'
}

function switchEditor(next: 'form' | 'json'): void {
  if (next === editorMode.value) return
  if (next === 'json') {
    jsonText.value = JSON.stringify(draft.value, null, 2)
    jsonError.value = ''
    invalidFields.value = new Set()
  } else {
    const parsed = parseJsonObject(jsonText.value)
    if (!parsed) { validateJson(); return }
    draft.value = parsed
    invalidFields.value = new Set()
  }
  editorMode.value = next
}

async function saveCurrent(): Promise<void> {
  if (!selectedName.value || busy.value || invalidFields.value.size) return
  const payload = editorMode.value === 'json' ? parseJsonObject(jsonText.value) : draft.value
  if (!payload) { validateJson(); return }
  busy.value = true
  pageError.value = ''
  try {
    await replaceServiceConfig(serviceName.value, selectedName.value, { payload })
    const name = selectedName.value
    summaries.value = await listServiceConfigs(serviceName.value)
    await loadConfig(name)
    showToast(`${name}.json saved.`)
  } catch (error) { pageError.value = message(error) }
  finally { busy.value = false }
}

function blankPayload(): JsonObject {
  return { service_provider: { service: serviceName.value, provider: '', enabled: false, priority: 1, config: {} } }
}

function createPayloadFrom(payload: JsonObject): JsonObject {
  const copied = clone(payload)
  const source = isObject(copied.service_provider) ? copied.service_provider : {}
  copied.service_provider = { ...source, service: serviceName.value, enabled: false }
  return copied
}

async function openCreate(duplicate: boolean, sourceName?: string): Promise<void> {
  if (!confirmDiscard()) return
  if ((hasChanges.value || invalidFields.value.size > 0) && currentFile.value) {
    draft.value = clone(currentFile.value.payload)
    editorMode.value = 'form'
    invalidFields.value = new Set()
  }
  const source = duplicate ? sourceName || selectedName.value || '' : ''
  createIsDuplicate.value = duplicate
  createName.value = source ? `${source}_copy` : ''
  createTemplate.value = source
  createJson.value = JSON.stringify(blankPayload(), null, 2)
  createError.value = ''
  createOpen.value = true
  if (source) {
    if (currentFile.value?.config_name === source) createJson.value = JSON.stringify(createPayloadFrom(currentFile.value.payload), null, 2)
    else await changeTemplate()
  }
}

function closeCreate(): void { if (!createBusy.value) createOpen.value = false }

async function changeTemplate(): Promise<void> {
  if (!createTemplate.value) { createJson.value = JSON.stringify(blankPayload(), null, 2); return }
  createError.value = ''
  try {
    const file = await getServiceConfig(serviceName.value, createTemplate.value)
    createJson.value = JSON.stringify(createPayloadFrom(file.payload), null, 2)
  } catch (error) { createError.value = message(error) }
}

async function createConfiguration(): Promise<void> {
  const name = createName.value.trim().replace(/\.json$/i, '')
  if (!name || !/^[A-Za-z0-9._-]+$/.test(name) || name.includes('..')) { createError.value = 'Enter a valid file name.'; return }
  if (summaries.value.some(row => row.config_name === name)) { createError.value = 'A configuration with this name already exists.'; return }
  const payload = parseJsonObject(createJson.value)
  if (!payload || !isObject(payload.service_provider)) { createError.value = 'Enter a JSON object with a service_provider object.'; return }
  if (payload.service_provider.service !== serviceName.value) { createError.value = `service_provider.service must be "${serviceName.value}".`; return }
  if (typeof payload.service_provider.provider !== 'string' || !payload.service_provider.provider.trim()) { createError.value = 'Enter a provider in service_provider.provider.'; return }
  createBusy.value = true
  createError.value = ''
  try {
    await addServiceConfig(serviceName.value, { config_name: name, payload })
    createOpen.value = false
    summaries.value = await listServiceConfigs(serviceName.value)
    await loadConfig(name)
    showToast(`${name}.json created.`)
  } catch (error) { createError.value = message(error) }
  finally { createBusy.value = false }
}

async function removeConfiguration(name: string): Promise<void> {
  if (busy.value || (selectedName.value === name && !confirmDiscard())) return
  if (!window.confirm(`Permanently remove ${name}.json?`)) return
  busy.value = true
  pageError.value = ''
  try {
    await removeServiceConfig(serviceName.value, name)
    summaries.value = await listServiceConfigs(serviceName.value)
    if (selectedName.value === name) clearSelection()
    showToast(`${name}.json removed.`)
  } catch (error) { pageError.value = message(error) }
  finally { busy.value = false }
}

watch(serviceName, () => { void loadPage() }, { immediate: true })
onBeforeUnmount(clearToast)
onBeforeRouteLeave(() => confirmDiscard())
onBeforeRouteUpdate(() => confirmDiscard())
</script>

<style scoped>
.service-config-page { color: #172550; max-width: 1200px; margin: 0 auto; }
.cfg-breadcrumb { display: flex; align-items: center; gap: .35rem; color: #7583a0; font-size: .83rem; margin-bottom: .8rem; }
.cfg-breadcrumb strong { color: #334465; font-weight: 600; }
.cfg-page-heading { display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem; margin-bottom: 1.25rem; }
.cfg-page-heading h1 { margin: 0 0 .25rem; font-size: clamp(1.55rem, 2.3vw, 2.15rem); font-weight: 750; letter-spacing: -.025em; }
.cfg-page-heading p { margin: 0; color: #71809e; font-size: .88rem; }
.cfg-add-top { white-space: nowrap; }
.cfg-add-top i, .cfg-add-inline i { margin-right: .2rem; }
.cd-toast { position: fixed; bottom: 1.5rem; right: 1.5rem; z-index: 99999; display: flex; align-items: center; gap: .5rem; max-width: 360px; padding: .75rem 1rem; border-radius: 6px; color: #fff; font-size: .9rem; box-shadow: 0 4px 16px rgba(0, 0, 0, .25); }
.cd-toast i { font-size: 1.2rem; flex-shrink: 0; }
.cd-toast-success { background: #16a34a; }
.cfg-strategy-card, .cfg-config-card { background: #fff; border: 1px solid #e0e6f0; border-radius: 12px; box-shadow: 0 3px 16px rgba(25, 45, 90, .045); }
.cfg-strategy-card { padding: 1.1rem 1.2rem; margin-bottom: 1.1rem; }
.cfg-saving-label { color: #5570ad; font-size: .8rem; }
.cfg-strategy-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .75rem; }
.cfg-strategy-option { display: flex; align-items: flex-start; gap: .65rem; min-height: 84px; border: 1px solid #d7dfed; border-radius: 8px; padding: .85rem .95rem; cursor: pointer; margin: 0; }
.cfg-strategy-option.selected { border-color: #1c45ef; background: #f2f6ff; box-shadow: inset 0 0 0 1px #1c45ef; }
.cfg-strategy-option.unsupported { opacity: .55; cursor: not-allowed; }
.cfg-strategy-option input { accent-color: #1c45ef; width: 17px; height: 17px; margin-top: .1rem; flex: none; }
.cfg-strategy-option span { display: flex; flex-direction: column; gap: .3rem; min-width: 0; }
.cfg-strategy-option strong { color: #172550; font-size: .84rem; }
.cfg-strategy-option small { color: #6c7a99; line-height: 1.4; font-size: .78rem; font-weight: 400; }
.cfg-strategy-footnote { margin: .7rem 0 0; color: #7b88a2; font-size: .76rem; }
.cfg-inline-error { margin: .5rem 0 0; font-size: .82rem; }
.cfg-configurations { margin: 0 auto; }
.cfg-strategy-section-heading, .cfg-configurations-heading { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: .9rem; }
.cfg-strategy-section-heading h2, .cfg-configurations-heading h2 { display: flex; align-items: center; gap: .55rem; font-size: 1.22rem; font-weight: 730; margin: 0 0 .15rem; }
.cfg-configurations-heading { margin-top: 1.4rem; }
.cfg-configurations-heading h2 span { display: inline-grid; place-items: center; min-width: 1.55rem; height: 1.55rem; padding: 0 .35rem; border-radius: 999px; background: #e8efff; color: #2953c5; font-size: .77rem; }
.cfg-configurations-heading p { color: #7a87a1; font-size: .83rem; margin: 0; }
.cfg-add-inline { display: none; }
.cfg-list-message { border: 1px dashed #cbd6e7; border-radius: 12px; background: #fff; padding: 2rem 1.25rem; color: #6d7b99; font-size: .87rem; }
.cfg-card-list { display: grid; grid-template-columns: minmax(0, 1fr); gap: .9rem; }
.cfg-config-card { display: flex; flex-direction: column; min-width: 0; overflow: hidden; transition: border-color .18s, box-shadow .18s; }
.cfg-config-card.expanded { border: 2px solid #2364ed; box-shadow: 0 8px 28px rgba(34, 72, 151, .12); }
.cfg-card-header { display: flex; align-items: flex-start; gap: .4rem; min-width: 0; padding: .95rem 1rem .75rem; }
.cfg-card-icon { display: grid; place-items: center; width: 1.5rem; height: 2rem; color: #1d376d; font-size: 1.25rem; flex: none; }
.cfg-card-identity { flex: 1; min-width: 0; }
.cfg-card-identity h3 { margin: .12rem 0 .4rem; font-size: .94rem; font-weight: 730; overflow-wrap: anywhere; }
.cfg-card-meta { display: flex; align-items: center; gap: .4rem; flex-wrap: wrap; }
.cfg-card-controls { display: flex; align-items: flex-start; gap: .5rem; flex: none; }
.cfg-card-more { display: inline-grid; place-items: center; width: 1.5rem; height: 2rem; flex: none; border: 0; border-radius: 50%; background: transparent; color: #1c376c; font-size: 1rem; cursor: pointer; padding: 0; }
.cfg-card-more:hover { color: #1457ec; background: #edf4ff; }
.cfg-status-pill, .cfg-priority-pill { border-radius: 999px; padding: .2rem .6rem; font-size: .69rem; font-weight: 700; white-space: nowrap; }
.cfg-card-meta button.cfg-status-pill { border: 0; font-family: inherit; cursor: pointer; }
.cfg-card-meta button.cfg-status-pill:hover:not(:disabled) { filter: brightness(.95); }
.cfg-card-meta button.cfg-status-pill:focus-visible { outline: 2px solid #155bea; outline-offset: 2px; }
.cfg-card-meta button.cfg-status-pill:disabled { opacity: .6; cursor: not-allowed; }
.cfg-status-pill.active { color: #158149; background: #ddf8e9; }
.cfg-status-pill.inactive { color: #68748a; background: #e9edf3; }
.cfg-status-pill.invalid { color: #b33939; background: #ffe9e9; }
.cfg-priority-pill { color: #576984; background: #eef2f8; }
.cfg-card-quick-fields { display: grid; grid-template-columns: 1fr 1fr 1fr .65fr .8fr; gap: .7rem; margin: 0 1rem .85rem; padding: .8rem 0 0; border-top: 1px solid #e5eaf2; }
.cfg-quick-field { min-width: 0; }
.cfg-quick-field label { display: block; margin-bottom: .3rem; color: #63718c; font-size: .72rem; font-weight: 650; }
.cfg-quick-field .form-control { width: 100%; min-width: 0; min-height: 36px; border-color: #d7dfed; border-radius: 6px; font-size: .82rem; }
.cfg-quick-field .form-control[readonly] { color: #71809c; background: #f5f7fb; }
.cfg-card-invalid { margin: 0 1rem .85rem; padding: .7rem 0 0; color: #aa3d43; font-size: .8rem; border-top: 1px solid #e5eaf2; }
.cfg-card-actions { display: flex; gap: .35rem; flex: none; }
.cfg-card-actions button { display: inline-grid; place-items: center; width: 1.75rem; height: 1.75rem; padding: 0; border: 0; border-radius: 50%; color: #fff; font-size: .9rem; cursor: pointer; }
.cfg-card-actions .cfg-card-action-duplicate { background: #2563eb; }
.cfg-card-actions .cfg-card-action-duplicate:hover:not(:disabled) { background: #1d4ed8; }
.cfg-card-actions .cfg-card-action-remove { background: #ef4444; }
.cfg-card-actions .cfg-card-action-remove:hover:not(:disabled) { background: #dc2626; }
.cfg-card-actions button:disabled { opacity: .5; cursor: not-allowed; }
.cfg-card-actions button:focus-visible, .cfg-card-more:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 2px; }
.cfg-expanded-editor { min-width: 0; overflow: hidden; background: #fff; border-top: 1px solid #bdd2fa; scroll-margin-top: 5rem; }
.cfg-tabs { display: flex; gap: 0; padding: 0 1.25rem; border-bottom: 1px solid #e5eaf3; }
.cfg-tabs button { background: none; border: 0; border-bottom: 2px solid transparent; color: #70809e; padding: .78rem 1rem; font-weight: 600; font-size: .84rem; cursor: pointer; }
.cfg-tabs button.active { color: #1c45ef; border-color: #1c45ef; }
.cfg-editor-content { padding: 1.25rem; min-height: 220px; }
.cfg-form-section { border: 1px solid #e2e8f0; border-radius: 10px; padding: 1rem; margin-bottom: 1rem; }
.cfg-form-section h4 { font-size: .98rem; font-weight: 700; margin: 0 0 .9rem; }
.cfg-section-help { color: #71809c; font-size: .81rem; margin: -.25rem 0 1rem; }
.cfg-full-json { min-height: 450px; font: .8rem/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre; resize: vertical; }
.cfg-editor-footer { position: sticky; bottom: 0; z-index: 2; display: flex; align-items: center; justify-content: space-between; gap: 1rem; border-top: 1px solid #e4e9f2; background: #fff; padding: .85rem 1.15rem; font-size: .8rem; }
.cfg-footer-actions { display: flex; gap: .5rem; margin-left: auto; }
.cfg-unsaved { color: #b25f14; }
.cfg-saved { color: #6a7997; }
.cfg-panel-message { padding: 2rem 1.25rem; color: #72809b; font-size: .86rem; }
.cfg-dialog-backdrop { position: fixed; inset: 0; z-index: 1100; background: rgba(18, 28, 55, .55); display: flex; align-items: center; justify-content: center; padding: 1rem; }
.cfg-dialog { background: #fff; width: min(680px, 100%); max-height: min(90vh, 900px); display: flex; flex-direction: column; border-radius: 12px; box-shadow: 0 14px 50px rgba(0,0,0,.24); color: #172550; }
.cfg-dialog-header, .cfg-dialog-footer { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 1rem 1.2rem; }
.cfg-dialog-header { border-bottom: 1px solid #e2e8f0; }
.cfg-dialog-header h2 { font-size: 1.1rem; font-weight: 700; margin: 0; }
.cfg-dialog-header button { border: 0; background: none; color: #71809c; font-size: 1.5rem; line-height: 1; cursor: pointer; }
.cfg-dialog-body { padding: 1.2rem; overflow-y: auto; display: flex; flex-direction: column; }
.cfg-dialog-body label { font-size: .83rem; font-weight: 650; margin: 0 0 .4rem; }
.cfg-dialog-body small { color: #7583a0; margin: .3rem 0 1rem; }
.cfg-dialog-body .form-control { border-color: #d7dfed; border-radius: 7px; }
.cfg-name-input { display: flex; align-items: center; gap: .55rem; }
.cfg-name-input span { color: #687894; font-size: .86rem; }
.cfg-create-json { min-height: 240px; font: .78rem/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; resize: vertical; }
.cfg-dialog-footer { justify-content: flex-end; border-top: 1px solid #e2e8f0; }
@media (max-width: 1100px) { .cfg-card-quick-fields { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 700px) { .cfg-card-quick-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .cfg-page-heading { align-items: flex-start; } .cfg-page-heading p { display: none; } .cfg-add-top { display: none; } .cfg-add-inline { display: inline-block; } .cfg-strategy-card { padding: 1rem; } .cfg-strategy-options { grid-template-columns: minmax(0, 1fr); } .cfg-strategy-option { min-height: 0; } .cfg-configurations-heading { margin-top: 1.2rem; } .cfg-card-header { padding: .85rem; } .cfg-card-controls { gap: .2rem; } .cfg-card-more { width: 2rem; height: 2.4rem; } .cfg-card-actions button { width: 2.4rem; height: 2.4rem; } .cfg-card-quick-fields { margin: 0 .85rem .85rem; gap: .65rem; } .cfg-editor-content { padding: .85rem; } .cfg-form-section { padding: .85rem; } .cfg-editor-footer { padding: .7rem .85rem; } .cfg-footer-actions .btn { padding: .35rem .55rem; font-size: .8rem; } .cfg-dialog-backdrop { align-items: flex-end; padding: 0; } .cfg-dialog { max-height: 92vh; border-radius: 14px 14px 0 0; } }
</style>
