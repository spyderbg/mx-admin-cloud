<template>
  <section class="ivs" aria-labelledby="item-voices-heading" :aria-busy="loading || !!busyKey">
    <header class="ivs-header">
      <h2 id="item-voices-heading">{{ copy.title }} <span>({{ voiceCount }})</span></h2>
      <div class="ivs-summary">
        <div class="ivs-summary-buttons">
          <button
            v-for="action in voiceActions"
            :key="action.label"
            type="button"
            class="btn btn-sm ivs-action-button"
            :class="action.tone && `ivs-summary-action-${action.tone}`"
            :disabled="action.disabled"
            @click="action.onClick"
          >
            <i class="mdi" :class="action.icon" aria-hidden="true"></i>
            {{ action.label }}
          </button>
        </div>
        <MobileActionsMenu class="ivs-summary-menu" label="Voice actions" :actions="voiceActions" />
      </div>
    </header>
    <p v-if="loading && !data" class="ivs-loading" role="status">{{ copy.loading }}</p>
    <div v-else-if="loadFailed" class="ivs-notice ivs-error" role="alert"><span>{{ copy.loadError }}</span><button type="button" class="ivs-button" @click="load()">{{ copy.retry }}</button></div>
    <template v-if="data">
      <div class="ivs-groups">
        <section v-for="group in groups" :key="group.language" class="ivs-group" :aria-label="langLabel(group.language, language)">
          <header class="ivs-group-header">
            <button type="button" class="ivs-toggle" :aria-expanded="expanded.has(group.language)" :aria-controls="`voices-${group.language}`" @click="toggleGroup(group.language)">
              <i :class="expanded.has(group.language) ? 'mdi mdi-chevron-down' : 'mdi mdi-chevron-right'" aria-hidden="true"></i>
              <i :class="`flag-icon flag-icon-${langFlag(group.language)}`" aria-hidden="true"></i>
              <span class="ivs-language">{{ langLabel(group.language, language) }}</span>
              <span class="ivs-count">{{ group.voices.length }} {{ group.voices.length === 1 ? copy.voice : copy.voices }}</span>
              <span class="ivs-current" :class="{ 'ivs-no-selection': !group.selected }">{{ group.selected ? `${copy.inUse}: ${voiceTitle(group.selected)} · ${copy[group.selected.selected_version || 'original']}` : copy.noSelection }}</span>
            </button>
          </header>
          <div v-show="expanded.has(group.language)" :id="`voices-${group.language}`" class="ivs-group-body">
            <div class="ivs-cards">
              <ItemVoiceCard v-for="voice in group.voices" :key="voice.id" :voice="voice" :capabilities="capabilities"
                :selected="group.selected?.id === voice.id" :busy="busy" :optimizing="busyKey === `optimize:${voice.id}`"
                :active="activePlayer === voice.id" :revision="revision" @play="onPlay(voice.id)"
                @replace="openUpload(group.language, voice.id)" @drop="openUpload(group.language, voice.id, $event)"
                @delete="openDelete(voice)" @optimize="optimize(voice)" @select="select(voice, $event)" />
              <button
                type="button"
                class="ivs-add-card"
                :class="{ 'ivs-drop-active': dragLanguage === group.language }"
                :disabled="busy"
                :aria-label="`${copy.add}: ${langLabel(group.language, language)}`"
                @click="chooseLanguageFile(group.language)"
                @dragover.prevent.stop="onLanguageDrag($event, group.language)"
                @dragleave="leaveLanguageDrop($event)"
                @drop.prevent.stop="dropInLanguage($event, group.language)"
              >
                <i class="mdi" :class="busyKey === `upload-language:${group.language}` ? 'mdi-loading mdi-spin' : 'mdi-plus-circle-outline'" aria-hidden="true"></i>
                <strong>{{ copy.add }}</strong>
                <span>{{ busyKey === `upload-language:${group.language}` ? copy.saving : copy.addHint }}</span>
              </button>
            </div>
          </div>
        </section>
      </div>
      <input ref="languageFileInput" type="file" :accept="acceptedFormats" hidden @change="pickLanguageFile" />
    </template>

    <VoiceDialog v-if="dialog === 'generate'" :title="copy.generate" :busy="!!busyKey" @close="closeDialog" @submit="generate">
      <label class="ivs-field">{{ copy.text }}<input v-model="generationText" required :disabled="!!busyKey || !capabilities.generate" /></label>
      <label class="ivs-field">{{ copy.language }}<select v-model="formLanguage" :disabled="!!busyKey"><option v-for="optionLanguage in SUPPORTED_LANGUAGES" :key="optionLanguage" :value="optionLanguage">{{ langLabel(optionLanguage, language) }}</option></select></label>
      <label class="ivs-field">{{ copy.ttsConfiguration }}<select v-model="chosenConfigurationName" :disabled="!!busyKey || configurationsLoading || !availableConfigurations.length">
        <option v-if="!availableConfigurations.length" value="">{{ configurationsLoading ? copy.loading : copy.noConfigurations }}</option>
        <option v-for="configuration in availableConfigurations" :key="configuration.config_name" :value="configuration.config_name">{{ configuration.config_name }} · {{ configuration.provider }}</option>
      </select></label>
      <p v-if="!capabilities.generate" class="ivs-notice">{{ copy.generationUnavailable }}</p>
      <div v-if="formError" class="ivs-notice ivs-error" role="alert">{{ formError }}<button v-if="configurationsFailed" type="button" class="ivs-button" @click="loadConfigurations">{{ copy.retry }}</button></div>
      <fieldset class="ivs-settings" :disabled="!!busyKey || !chosenConfiguration || !capabilities.generate"><legend>{{ copy.settings }}</legend>
        <label class="ivs-range"><span>{{ copy.speed }}<output>{{ speed.toFixed(2) }}×</output></span><input v-model.number="speed" :aria-label="copy.speed" type="range" :min="speedLimits.min" :max="speedLimits.max" step="0.05" /></label>
        <label v-if="chosenConfiguration?.stability !== undefined" class="ivs-range"><span>{{ copy.stability }}<output>{{ Math.round(stability * 100) }}%</output></span><input v-model.number="stability" :aria-label="copy.stability" type="range" min="0" max="1" step="0.01" /></label>
        <label v-if="chosenConfiguration?.similarity !== undefined" class="ivs-range"><span>{{ copy.similarity }}<output>{{ Math.round(similarity * 100) }}%</output></span><input v-model.number="similarity" :aria-label="copy.similarity" type="range" min="0" max="1" step="0.01" /></label>
        <label class="ivs-checkbox"><span>{{ copy.autoOptimize }}</span><input v-model="autoOptimize" type="checkbox" :disabled="!capabilities.optimize" role="switch" /></label>
      </fieldset>
      <p class="ivs-notice">{{ copy.keepSelection }}</p>
      <template #footer><button type="submit" class="ivs-button ivs-primary ivs-submit" :disabled="!canGenerate"><i class="mdi" :class="busyKey ? 'mdi-loading mdi-spin' : 'mdi-auto-fix'" aria-hidden="true"></i>{{ busyKey ? copy.generating : copy.generate }}</button></template>
    </VoiceDialog>

    <VoiceDialog v-if="dialog === 'upload'" :title="uploadMode === 'replace' ? copy.replace : copy.add" :busy="!!busyKey" @close="closeDialog" @submit="upload">
      <label class="ivs-field">{{ copy.language }}<select v-model="formLanguage" :disabled="!!busyKey"><option v-for="optionLanguage in SUPPORTED_LANGUAGES" :key="optionLanguage" :value="optionLanguage">{{ langLabel(optionLanguage, language) }}</option></select></label>
      <label v-if="uploadMode === 'replace'" class="ivs-field">{{ copy.replaceTarget }}<select v-model="uploadTarget" required :disabled="!!busyKey"><option v-for="voice in uploadVoices" :key="voice.id" :value="voice.id">{{ voiceTitle(voice) }}</option></select></label>
      <p v-if="cannotAdd" class="ivs-notice">{{ copy.addUnavailable }}</p>
      <div v-if="formError" class="ivs-notice ivs-error" role="alert">{{ formError }}</div>
      <div class="ivs-upload-drop" :class="{ 'ivs-drop-active': uploadDragging }" @dragover.prevent="uploadDragging = !busyKey" @dragleave.self="uploadDragging = false" @drop.prevent="onUploadDrop">
        <i class="mdi mdi-cloud-upload" aria-hidden="true"></i><strong>{{ copy.dropHere }}</strong><span>{{ copy.or }}</span>
        <button type="button" class="ivs-button" :disabled="!!busyKey" @click="fileInput?.click()">{{ copy.browse }}</button><small>{{ formatsLabel }}</small>
        <input ref="fileInput" type="file" :accept="acceptedFormats" hidden @change="pickFile" />
      </div>
      <div v-if="uploadFile" class="ivs-file"><i class="mdi mdi-file-music" aria-hidden="true"></i><div><strong>{{ uploadFile.name }}</strong><small>{{ copy.ready }}</small></div><button type="button" :disabled="!!busyKey" :aria-label="copy.remove" @click="setFile(null)"><i class="mdi mdi-close" aria-hidden="true"></i></button></div>
      <div v-if="uploadUrl" class="ivs-upload-preview"><strong>{{ copy.preview }}</strong><VoiceAudioPreview :src="uploadUrl" :label="uploadFile?.name || copy.preview" :active="activePlayer === 'upload-preview'" @play="onPlay('upload-preview')" /></div>
      <p class="ivs-notice">{{ uploadMode === 'replace' ? copy.replaceHint : capabilities.multiple_voices ? copy.keepSelection : '' }}</p>
      <template #footer><button v-if="uploadMode === 'replace'" type="button" class="ivs-button" :disabled="!!busyKey" @click="closeDialog">{{ copy.cancel }}</button><button type="submit" class="ivs-button ivs-primary ivs-submit" :disabled="!canUpload"><i class="mdi" :class="busyKey ? 'mdi-loading mdi-spin' : 'mdi-upload'" aria-hidden="true"></i>{{ busyKey ? copy.saving : uploadMode === 'replace' ? copy.replace : copy.add }}</button></template>
    </VoiceDialog>

    <VoiceDialog v-if="dialog === 'delete' && deleteTarget" :title="copy.deleteVoice" :busy="!!busyKey" @close="closeDialog" @submit="deleteVoice">
      <p class="ivs-delete-confirm">{{ copy.deleteConfirm.replace('{voice}', voiceTitle(deleteTarget)) }}</p>
      <p v-if="formError" class="ivs-notice ivs-error" role="alert">{{ formError }}</p>
      <p class="ivs-notice">{{ copy.deleteHelp }}</p>
      <template #footer><button type="button" class="ivs-button" :disabled="!!busyKey" @click="closeDialog">{{ copy.cancel }}</button><button type="submit" class="ivs-button ivs-danger" :disabled="!!busyKey"><i class="mdi" :class="busyKey ? 'mdi-loading mdi-spin' : 'mdi-delete'" aria-hidden="true"></i>{{ busyKey ? copy.deleting : copy.deleteVoice }}</button></template>
    </VoiceDialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Voice, Language } from '../types/viewModels'
import { SUPPORTED_LANGUAGES } from '../types/viewModels'
import type { AudioVersion, ItemVoiceList, ItemVoiceRecord } from '../types/itemVoices'
import type { GenerateItemVoiceResponse } from '../types/contentSchemas'
import { legacyVoiceCapabilities } from '../types/itemVoices'
import { deleteItemVoice, generateItemVoice, listItemVoices, optimizeItemVoice, selectItemVoice, uploadItemVoice } from '../api/itemVoicesClient'
import { getServiceConfig, listServiceConfigs } from '../api/configurationClient'
import type { ConfigurationFileResponse } from '../types/configurationSchemas'
import { orderConfigurationFiles } from '../utils/configurationNavigation'
import { speedRange, ttsConfigurationOption } from '../utils/ttsConfigurations'
import { readVoiceGenerationPreferences, writeVoiceGenerationPreferences, voiceGenerationSettingsFor } from '../utils/voiceGenerationPreferences'
import { invalidateItemDetail } from '../api/cache'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'
import { selectedVoice, validateAudioFile, voiceErrorMessage, voiceTitle, voiceSelectionId } from '../utils/itemVoices'
import { langFlag, langLabel } from '../utils/voiceLabels'
import ItemVoiceCard from './ItemVoiceCard.vue'
import MobileActionsMenu, { type MobileAction } from './MobileActionsMenu.vue'
import VoiceDialog from './VoiceDialog.vue'
import VoiceAudioPreview from './VoiceAudioPreview.vue'

const props = defineProps<{ categoryId: string; itemId: string; itemName: string; voices: Voice[]; externalPlayingId: string | null; disabled: boolean }>()
const emit = defineEmits<{ changed: []; play: []; toast: [message: string, variant: 'success' | 'error'] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
const data = ref<ItemVoiceList | null>(null)
const loading = ref(false)
const loadFailed = ref(false)
const busyKey = ref('')
const busy = computed(() => !!busyKey.value || props.disabled || loading.value)
const capabilities = computed(() => ({ ...legacyVoiceCapabilities, ...data.value?.capabilities }))
const formats = computed(() => capabilities.value.upload_formats.length ? capabilities.value.upload_formats : ['mp3'])
const formatsLabel = computed(() => formats.value.map((format) => format.toUpperCase()).join(', '))
const acceptedFormats = computed(() => formats.value.map((format) => `.${format}`).join(','))
const expanded = ref(new Set<Language>())
const voiceActions = computed<MobileAction[]>(() => [
  {
    label: copy.value.add,
    icon: 'mdi-plus',
    tone: 'primary',
    disabled: busy.value,
    onClick: () => openUpload(language.value),
  },
  {
    label: copy.value.generate,
    icon: 'mdi-auto-fix',
    tone: 'regenerate',
    disabled: busy.value,
    onClick: () => openGenerate(language.value),
  },
  {
    label: expanded.value.size ? copy.value.collapse : copy.value.expand,
    icon: expanded.value.size ? 'mdi-chevron-up' : 'mdi-chevron-down',
    tone: 'primary',
    onClick: toggleAll,
  },
])
const activePlayer = ref<string | null>(null)
const revision = ref(0)
const groups = computed(() => SUPPORTED_LANGUAGES.map((language) => {
  const voices = (data.value?.voices || []).filter((voice) => voice.language === language && voice.file_uri)
  return { language, voices, selected: selectedVoice(voices, capabilities.value) }
}))
const voiceCount = computed(() => groups.value.reduce((count, group) => count + group.voices.length, 0))
const identity = computed(() => `${props.categoryId}/${props.itemId}`)
let loadController: AbortController | null = null
let configurationRequest = 0
let operation = 0

async function load(): Promise<boolean> {
  loadController?.abort()
  const controller = new AbortController()
  loadController = controller
  const key = identity.value
  loading.value = true
  loadFailed.value = false
  try {
    const result = await listItemVoices(props.categoryId, props.itemId, controller.signal)
    if (key !== identity.value || controller.signal.aborted) return false
    data.value = { ...result, voices: result.voices || [], name: result.name || {} }
    return true
  } catch {
    if (!controller.signal.aborted && key === identity.value) loadFailed.value = true
    return false
  } finally {
    if (loadController === controller) loading.value = false
  }
}

watch(identity, () => {
  operation++
  configurationRequest++
  busyKey.value = ''
  closeDialog()
  data.value = null
  activePlayer.value = null
  expanded.value = new Set()
  void load()
})
// Also refresh after the existing summary cards regenerate, rename or replace audio.
watch(() => props.voices, () => { if (!busyKey.value) { activePlayer.value = null; revision.value++; void load() } })
watch(() => props.externalPlayingId, (id) => { if (id) activePlayer.value = null })
function onPlay(id: string): void { activePlayer.value = id; emit('play') }
function toggleGroup(language: Language): void {
  const next = new Set(expanded.value)
  if (next.has(language)) next.delete(language); else next.add(language)
  expanded.value = next
}
function toggleAll(): void { expanded.value = expanded.value.size ? new Set() : new Set(SUPPORTED_LANGUAGES) }
const dialog = ref<'upload' | 'generate' | 'delete' | null>(null)
const formLanguage = ref<Language>('en')
const formError = ref('')
const deleteTarget = ref<ItemVoiceRecord | null>(null)
function closeDialog(): void {
  if (busyKey.value) return
  rememberGenerationSettings()
  configurationRequest++
  dialog.value = null
  formError.value = ''
  activePlayer.value = null
  deleteTarget.value = null
  setFile(null)
}

type ToastVariant = 'success' | 'error'
async function mutate<T>(key: string, action: () => Promise<T>, success: string | ((result: T) => [string, ToastVariant])): Promise<void> {
  if (busy.value) return
  const id = ++operation
  const categoryId = props.categoryId
  const itemId = props.itemId
  busyKey.value = key
  formError.value = ''
  activePlayer.value = null
  try {
    const result = await action()
    invalidateItemDetail(categoryId, itemId)
    if (operation !== id) return
    revision.value++
    const refreshed = await load()
    if (operation !== id) return
    busyKey.value = ''
    closeDialog()
    const [message, variant] = typeof success === 'string' ? [success, 'success' as const] : success(result)
    emit('toast', refreshed ? message : copy.value.refreshError, refreshed ? variant : 'error')
    emit('changed')
  } catch (error) {
    if (operation !== id) return
    const message = voiceErrorMessage(error) || copy.value.operationError
    if (dialog.value) formError.value = message
    else emit('toast', message, 'error')
  } finally {
    if (operation === id) busyKey.value = ''
  }
}
function optimize(voice: ItemVoiceRecord): void {
  if (!capabilities.value.optimize) return
  void mutate(`optimize:${voice.id}`, () => optimizeItemVoice(props.categoryId, props.itemId, voice.id), copy.value.optimizedSuccess)
}
function select(voice: ItemVoiceRecord, version: AudioVersion): void {
  const voiceId = voiceSelectionId(voice, version)
  if (!capabilities.value.select || !voiceId) return
  const success = copy.value.selectedSuccess
    .replace('{voice}', voiceTitle(voice))
    .replace('{language}', langLabel(voice.language, language.value))
  void mutate(`select:${voice.id}`, () => selectItemVoice(props.categoryId, props.itemId, voiceId), success)
}
function openDelete(voice: ItemVoiceRecord): void {
  if (busy.value) return
  deleteTarget.value = voice
  dialog.value = 'delete'
}
function deleteVoice(): void {
  const voice = deleteTarget.value
  if (!voice) return
  void mutate(`delete:${voice.id}`, () => deleteItemVoice(props.categoryId, props.itemId, voice.id), copy.value.deletedSuccess)
}

const configurationFiles = ref<ConfigurationFileResponse[]>([])
const availableConfigurations = computed(() => configurationFiles.value
  .map((file) => ttsConfigurationOption(file, formLanguage.value))
  .filter((configuration) => configuration !== null))
const generationPreferences = ref(readVoiceGenerationPreferences())
const chosenConfigurationName = ref('')
const chosenConfiguration = computed(() => availableConfigurations.value.find((configuration) => configuration.config_name === chosenConfigurationName.value))
const configurationsLoading = ref(false)
const configurationsFailed = ref(false)
const generationText = ref('')
const speed = ref(1)
const speedLimits = computed(() => speedRange(chosenConfiguration.value?.provider))
const stability = ref(.5)
const similarity = ref(.75)
const autoOptimize = ref(false)
const canGenerate = computed(() => !busy.value && capabilities.value.generate && !!chosenConfiguration.value && !!generationText.value.trim() && !configurationsLoading.value)
watch(chosenConfiguration, (configuration) => {
  if (!configuration || dialog.value !== 'generate') return
  const settings = voiceGenerationSettingsFor(configuration, generationPreferences.value.settings[formLanguage.value])
  speed.value = settings.speed
  stability.value = settings.stability
  similarity.value = settings.similarity
  autoOptimize.value = settings.autoOptimize
}, { flush: 'sync' })
function rememberGenerationSettings(): void {
  if (dialog.value !== 'generate' || configurationsLoading.value || !chosenConfiguration.value) return
  generationPreferences.value = {
    language: formLanguage.value,
    settings: { ...generationPreferences.value.settings, [formLanguage.value]: {
      configurationName: chosenConfigurationName.value, speed: speed.value,
      stability: stability.value, similarity: similarity.value, autoOptimize: autoOptimize.value,
    } },
  }
  writeVoiceGenerationPreferences(generationPreferences.value)
}
watch([formLanguage, chosenConfigurationName, speed, stability, similarity, autoOptimize, configurationsLoading], rememberGenerationSettings)
function chooseConfiguration(): void {
  const previous = generationPreferences.value.settings[formLanguage.value]?.configurationName || chosenConfigurationName.value
  chosenConfigurationName.value = availableConfigurations.value.find((configuration) => configuration.config_name === previous)?.config_name
    || availableConfigurations.value.find((configuration) => configuration.enabled)?.config_name
    || availableConfigurations.value[0]?.config_name || ''
}
async function loadConfigurations(): Promise<void> {
  const request = ++configurationRequest
  configurationFiles.value = []
  chosenConfigurationName.value = ''
  configurationsFailed.value = false
  formError.value = ''
  configurationsLoading.value = true
  try {
    const summaries = orderConfigurationFiles(await listServiceConfigs('text_to_speech'))
    if (request !== configurationRequest) return
    const files = await Promise.all(summaries.filter((summary) => summary.valid_json)
      .map((summary) => getServiceConfig('text_to_speech', summary.config_name)))
    if (request !== configurationRequest) return
    configurationFiles.value = files
    chooseConfiguration()
  } catch {
    if (request === configurationRequest) { configurationsFailed.value = true; formError.value = copy.value.configurationsError }
  } finally {
    if (request === configurationRequest) configurationsLoading.value = false
  }
}
function openGenerate(language: Language): void {
  if (busy.value) return
  activePlayer.value = null
  formLanguage.value = generationPreferences.value.language ?? language
  generationText.value = data.value?.name[formLanguage.value] || ''
  autoOptimize.value = generationPreferences.value.settings[formLanguage.value]?.autoOptimize ?? false
  dialog.value = 'generate'
  void loadConfigurations()
}
function generate(): void {
  if (!canGenerate.value || !chosenConfiguration.value) return
  const body = { language: formLanguage.value, text: generationText.value.trim(), configuration_name: chosenConfiguration.value.config_name, speed: speed.value,
    stability: chosenConfiguration.value.stability === undefined ? undefined : stability.value,
    similarity: chosenConfiguration.value.similarity === undefined ? undefined : similarity.value,
    optimize: autoOptimize.value && capabilities.value.optimize }
  expanded.value = new Set([...expanded.value, formLanguage.value])
  void mutate('generate', () => generateItemVoice(props.categoryId, props.itemId, body), generatedToast)
}
// The new take is kept even when optimizing it fails, so that is reported, not raised.
function generatedToast(result: GenerateItemVoiceResponse): [string, ToastVariant] {
  const warnings = result.warnings || []
  return warnings.length ? [`${copy.value.generatedWithWarnings} ${warnings.join(' ')}`, 'error'] : [copy.value.generatedSuccess, 'success']
}

const uploadMode = ref<'add' | 'replace'>('add')
const uploadTarget = ref('')
const uploadFile = ref<File | null>(null)
const uploadUrl = ref('')
const uploadDragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const languageFileInput = ref<HTMLInputElement | null>(null)
const languageFileInputLanguage = ref<Language | null>(null)
const uploadVoices = computed(() => groups.value.find((group) => group.language === formLanguage.value)?.voices || [])
const cannotAdd = computed(() => uploadMode.value === 'add' && !capabilities.value.multiple_voices && uploadVoices.value.length > 0)
const canUpload = computed(() => !busy.value && !!uploadFile.value && !cannotAdd.value && (uploadMode.value === 'add' || uploadVoices.value.some((voice) => voice.id === uploadTarget.value)))
watch(formLanguage, () => {
  uploadTarget.value = uploadVoices.value[0]?.id || ''
  if (!uploadVoices.value.length) uploadMode.value = 'add'
  if (dialog.value === 'generate') {
    generationText.value = data.value?.name[formLanguage.value] || ''
    chooseConfiguration()
  }
}, { flush: 'sync' })
function setFile(file: File | null): void {
  if (uploadUrl.value) URL.revokeObjectURL(uploadUrl.value)
  activePlayer.value = null
  uploadUrl.value = ''
  uploadFile.value = null
  formError.value = ''
  if (!file) return
  const error = validateAudioFile(file, formats.value)
  if (error) { formError.value = error === 'empty' ? copy.value.emptyFile : `${copy.value.invalidFile} ${formatsLabel.value}`; return }
  uploadFile.value = file
  uploadUrl.value = URL.createObjectURL(file)
}
function acceptFiles(files: FileList | null): void {
  if (busyKey.value || !files?.length) return
  if (files.length > 1) { setFile(null); formError.value = copy.value.oneFile; return }
  setFile(files[0])
}
function pickFile(event: Event): void {
  const input = event.target as HTMLInputElement
  acceptFiles(input.files)
  input.value = ''
}
function onUploadDrop(event: DragEvent): void { uploadDragging.value = false; acceptFiles(event.dataTransfer?.files || null) }
function chooseLanguageFile(language: Language): void {
  if (busy.value) return
  languageFileInputLanguage.value = language
  if (languageFileInput.value) languageFileInput.value.value = ''
  languageFileInput.value?.click()
}
function pickLanguageFile(event: Event): void {
  const input = event.target as HTMLInputElement
  const language = languageFileInputLanguage.value
  const files = input.files
  input.value = ''
  if (language) void uploadLanguageFiles(language, files)
}
async function uploadLanguageFiles(language: Language, files: FileList | null): Promise<void> {
  if (busy.value || !files?.length) return
  if (files.length > 1) {
    emit('toast', copy.value.oneFile, 'error')
    return
  }
  const file = files[0]
  const error = validateAudioFile(file, formats.value)
  if (error) {
    emit('toast', error === 'empty' ? copy.value.emptyFile : `${copy.value.invalidFile} ${formatsLabel.value}`, 'error')
    return
  }
  expanded.value = new Set([...expanded.value, language])
  await mutate(
    `upload-language:${language}`,
    () => uploadItemVoice(props.categoryId, props.itemId, language, file),
    copy.value.saved,
  )
}
function openUpload(language: Language, voiceId?: string, files?: FileList): void {
  if (busy.value) return
  formLanguage.value = language
  uploadMode.value = voiceId ? 'replace' : 'add'
  uploadTarget.value = voiceId || uploadVoices.value[0]?.id || ''
  setFile(null)
  dialog.value = 'upload'
  if (files) acceptFiles(files)
}
function upload(): void {
  if (!canUpload.value || !uploadFile.value) return
  const file = uploadFile.value
  const language = formLanguage.value
  const voiceId = uploadMode.value === 'replace' ? uploadTarget.value : undefined
  expanded.value = new Set([...expanded.value, language])
  void mutate('upload', () => uploadItemVoice(props.categoryId, props.itemId, language, file, voiceId), copy.value.saved)
}
const dragLanguage = ref<Language | null>(null)
function onLanguageDrag(event: DragEvent, language: Language): void {
  if (busy.value || !event.dataTransfer?.types.includes('Files')) return
  event.dataTransfer.dropEffect = 'copy'
  dragLanguage.value = language
}
function leaveLanguageDrop(event: DragEvent): void {
  if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node | null)) dragLanguage.value = null
}
function dropInLanguage(event: DragEvent, language: Language): void {
  dragLanguage.value = null
  if (event.dataTransfer?.files.length) void uploadLanguageFiles(language, event.dataTransfer.files)
}
void load()
onBeforeUnmount(() => {
  rememberGenerationSettings()
  operation++
  loadController?.abort()
  configurationRequest++
  if (uploadUrl.value) URL.revokeObjectURL(uploadUrl.value)
})
</script>

<style scoped>
.ivs { container-type: inline-size; container-name: voices; color: #111a3d; border: 1px solid #e6eaf4; border-radius: 10px; background: #fff; padding: 1.35rem; margin-bottom: 1rem; box-shadow: 0 4px 18px #19264d0a; }
.ivs-header { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.25rem; }
.ivs-header h2 { color: #111a3d; font-size: 1.05rem; font-weight: 700; margin: 0; }
.ivs-header h2 span { margin-left: .35rem; }
.ivs-summary { display: flex; align-items: center; }
.ivs-summary-buttons { display: none; gap: .55rem; }
.ivs-action-button { white-space: nowrap; }
.ivs-summary-action-primary { border: 1px solid #2563eb; background: #eef4ff; color: #2563eb; }
.ivs-summary-action-regenerate { border: 1px solid #0f766e; background: #e6f4f3; color: #0f766e; }
.ivs-summary-action-primary:hover,
.ivs-summary-action-regenerate:hover { filter: brightness(.95); }
.ivs-button { display: inline-flex; align-items: center; justify-content: center; gap: .4rem; border: 1px solid #818cf8; border-radius: 6px; background: white; color: #4f46e5; font-weight: 600; font-size: .8rem; padding: .5rem .85rem; min-height: 38px; cursor: pointer; line-height: 1.3; }
.ivs-button:hover:not(:disabled) { background: #eef2ff; }
.ivs-primary { background: #4f46e5; color: white; border-color: #4f46e5; }
.ivs-primary:hover:not(:disabled) { background: #4338ca; }
.ivs-danger { border-color: #ef4444; background: #ef4444; color: white; }
.ivs-danger:hover:not(:disabled) { border-color: #dc2626; background: #dc2626; }
.ivs-button:disabled { opacity: .5; cursor: default; }
.ivs-button i { font-size: 1.1rem; }
.ivs button { scroll-margin-top: 80px; }
.ivs-groups { display: grid; gap: .85rem; }
.ivs-group { border: 1px solid #b8d4f0; border-radius: 9px; min-width: 0; }
.ivs-group-header { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem; justify-content: space-between; border-radius: 9px; padding: .55rem .8rem; background: #f0f7ff; }
.ivs-toggle { display: flex; align-items: center; gap: .7rem; flex: 1 1 26rem; min-width: 0; min-height: 40px; text-align: left; border: 0; border-radius: 5px; padding: 0; color: #111a3d; background: transparent; cursor: pointer; }
.ivs-toggle > .mdi { font-size: 1.25rem; }
.ivs-toggle > .flag-icon { font-size: 1.3rem; flex: none; }
.ivs-language { font-size: .98rem; font-weight: 700; }
.ivs-count { white-space: nowrap; font-size: .75rem; color: #63708c; }
.ivs-current { color: #24704f; font-size: .74rem; padding-left: .85rem; border-left: 1px solid #cddced; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ivs-no-selection { color: #64748b; }
.ivs-group-body { border-top: 1px solid #dbe6f4; padding: .9rem; }
.ivs-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: stretch; gap: .9rem; }
.ivs-add-card { min-width: 0; min-height: 13rem; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: .55rem; padding: 1rem; border: 1px dashed #93baf2; border-radius: 10px; background: #f8fbff; color: #334155; font: inherit; cursor: pointer; }
.ivs-add-card > i { color: #4f46e5; font-size: 2rem; }
.ivs-add-card strong { color: #173d86; font-size: 1rem; }
.ivs-add-card span { max-width: 16rem; color: #63708c; font-size: .8rem; line-height: 1.45; }
.ivs-add-card:hover:not(:disabled) { background: #f1f6ff; border-color: #4f46e5; }
.ivs-add-card:disabled { cursor: default; opacity: .6; }
.ivs-drop-active { background: #e0e7ff !important; outline: 2px dashed #4f46e5; outline-offset: -4px; }
.ivs-notice { display: flex; align-items: center; justify-content: space-between; gap: .65rem; border-radius: 7px; background: #eff6ff; color: #475569; padding: .8rem 1rem; font-size: .8rem; line-height: 1.5; margin: 0 0 1rem; }
.ivs-notice:empty { display: none; }
.ivs-notice > button:not(.ivs-button), .ivs-file > button { flex: none; display: grid; place-items: center; min-width: 36px; min-height: 36px; border: 0; background: transparent; color: inherit; border-radius: 5px; font-size: 1.2rem; cursor: pointer; }
.ivs-error { background: #fff1f2; color: #9f1239; }
.ivs-empty, .ivs-loading { color: #64748b; text-align: center; padding: 1.5rem .5rem; font-size: .9rem; }
.ivs-dialog-language { display: flex; align-items: center; gap: .8rem; background: #f1f7ff; padding: 1rem; border-radius: 7px; margin-bottom: 1.25rem; }
.ivs-dialog-language > i { font-size: 1.5rem; }
.ivs-dialog-language small, .ivs-file small { display: block; color: #63708c; margin-top: .3rem; }
.ivs-field { display: flex; flex-direction: column; gap: .45rem; font-size: .85rem; font-weight: 600; margin-bottom: 1.1rem; }
.ivs-field input, .ivs-field select { width: 100%; min-height: 44px; border: 1px solid #cbd8ec; border-radius: 6px; background: #fff; color: #111a3d; padding: .6rem .7rem; font-size: .9rem; font-weight: 400; }
.ivs-field input[readonly], .ivs-field input:disabled, .ivs-field select:disabled { background: #f8fafc; color: #64748b; }
.ivs-settings { border: 0; padding: .5rem 0 0; margin: 0 0 1.25rem; }
.ivs-settings legend { font-size: 1rem; font-weight: 700; margin-bottom: 1rem; }
.ivs-range { display: block; margin-bottom: .8rem; font-size: .85rem; }
.ivs-range > span { display: flex; justify-content: space-between; font-weight: 600; }
.ivs-range input { width: 100%; height: 32px; accent-color: #4f46e5; }
.ivs-checkbox { display: flex; justify-content: space-between; align-items: center; gap: 1rem; margin: .5rem 0 0; font-size: .86rem; }
.ivs-checkbox input { accent-color: #2f8f68; width: 22px; height: 22px; }
.ivs-submit { flex: 1; min-height: 44px; }
.ivs-upload-drop { display: flex; flex-direction: column; align-items: center; gap: .55rem; text-align: center; border: 1px dashed #93baf2; padding: 1.5rem; background: #f8fbff; border-radius: 8px; }
.ivs-upload-drop > i { color: #4f46e5; font-size: 2rem; }
.ivs-upload-drop > span, .ivs-upload-drop small { color: #64748b; font-size: .8rem; }
.ivs-file { display: flex; align-items: center; gap: .8rem; padding: .85rem; border-radius: 7px; background: #f1f7ff; margin-top: .8rem; }
.ivs-file > i { font-size: 1.6rem; }
.ivs-file > div { flex: 1; min-width: 0; font-size: .85rem; overflow-wrap: anywhere; }
.ivs-delete-confirm { margin: 0 0 1rem; line-height: 1.5; }
.ivs-upload-preview { margin: 1.2rem 0; font-size: .85rem; }
.ivs-upload-preview > strong { display: block; margin-bottom: .6rem; }
button:focus-visible, input:focus-visible, select:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 2px; }
@container voices (max-width: 65rem) {
  .ivs-current { display: none; }
  .ivs-toggle { flex-basis: 15rem; }
  .ivs-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@container item-details (min-width: 64rem) {
  .ivs-summary-buttons { display: flex; }
  .ivs-summary-menu { display: none; }
}
@container voices (max-width: 42rem) {
  .ivs-cards { grid-template-columns: minmax(0, 1fr); }
  .ivs-group-body { padding: .65rem; }
}
@media (max-width: 575px) {
  .ivs { padding: .8rem; }
  .ivs-header { gap: .8rem; }
  .ivs-header h2 { font-size: 1.3rem; }
  .ivs-toggle { min-height: 44px; }
  .ivs-button { min-height: 44px; padding: .5rem .6rem; font-size: .76rem; }
  .ivs-group-header { padding: .5rem .6rem; }
  .ivs-language { font-size: .92rem; }
  .ivs-toggle { gap: .5rem; }
  .ivs-notice { padding: .7rem; }
  .ivs-file > button, .ivs-notice > button:not(.ivs-button) { min-height: 44px; min-width: 44px; }
  .ivs-field input, .ivs-field select { font-size: 1rem; }
}
</style>
