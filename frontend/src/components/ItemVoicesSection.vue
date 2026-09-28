<template>
  <section class="ivs" aria-labelledby="item-voices-heading" :aria-busy="loading || !!busyKey">
    <header class="ivs-header">
      <div><h2 id="item-voices-heading">{{ copy.title }}</h2><p>{{ copy.description }}</p></div>
      <div class="ivs-summary"><span>{{ groups.length }} {{ copy.languages }} · {{ voiceCount }} {{ copy.voices }}</span>
        <button type="button" class="ivs-button" @click="toggleAll"><i :class="expanded.size ? 'mdi mdi-chevron-up' : 'mdi mdi-chevron-down'" aria-hidden="true"></i>{{ expanded.size ? copy.collapse : copy.expand }}</button>
      </div>
    </header>
    <p v-if="mockMode" class="ivs-notice ivs-demo"><i class="mdi mdi-information-outline" aria-hidden="true"></i>{{ copy.demo }}</p>
    <div v-if="notice" class="ivs-notice" :class="{ 'ivs-error': notice.error }" :role="notice.error ? 'alert' : 'status'">
      <span>{{ notice.text }}</span><button type="button" :aria-label="copy.close" @click="notice = null"><i class="mdi mdi-close" aria-hidden="true"></i></button>
    </div>
    <p v-if="loading && !data" class="ivs-loading" role="status">{{ copy.loading }}</p>
    <div v-else-if="loadFailed" class="ivs-notice ivs-error" role="alert"><span>{{ copy.loadError }}</span><button type="button" class="ivs-button" @click="load()">{{ copy.retry }}</button></div>
    <template v-if="data">
      <p v-if="!capabilities.multiple_voices || !capabilities.generate || !capabilities.optimize || !capabilities.select" class="ivs-notice ivs-availability">{{ copy.legacy }}</p>
      <div class="ivs-groups">
        <section v-for="group in groups" :key="group.language" class="ivs-group" :aria-label="langLabel(group.language)">
          <header class="ivs-group-header">
            <button type="button" class="ivs-toggle" :aria-expanded="expanded.has(group.language)" :aria-controls="`voices-${group.language}`" @click="toggleGroup(group.language)">
              <i :class="expanded.has(group.language) ? 'mdi mdi-chevron-down' : 'mdi mdi-chevron-right'" aria-hidden="true"></i>
              <i :class="`flag-icon flag-icon-${langFlag(group.language)}`" aria-hidden="true"></i>
              <span class="ivs-language">{{ langLabel(group.language) }}</span>
              <span class="ivs-count">{{ group.voices.length }} {{ group.voices.length === 1 ? copy.voice : copy.voices }}</span>
              <span class="ivs-current" :class="{ 'ivs-no-selection': !group.selected }">{{ group.selected ? `${copy.inUse}: ${voiceTitle(group.selected)} · ${copy[group.selected.selected_version || 'original']}` : copy.noSelection }}</span>
            </button>
            <div class="ivs-group-actions">
              <button type="button" class="ivs-button" :disabled="busy" @click="openUpload(group.language)"><i class="mdi mdi-plus" aria-hidden="true"></i>{{ copy.add }}</button>
              <button type="button" class="ivs-button ivs-primary" :disabled="busy" @click="openGenerate(group.language)"><i class="mdi mdi-auto-fix" aria-hidden="true"></i>{{ copy.generate }}</button>
            </div>
          </header>
          <div v-show="expanded.has(group.language)" :id="`voices-${group.language}`" class="ivs-group-body">
            <div class="ivs-base"><span>{{ copy.baseVoice }}</span>
              <button type="button" :disabled="busy" @click="openGenerate(group.language)">{{ baseVoiceLabel(group.language) }}<i class="mdi mdi-chevron-down" aria-hidden="true"></i></button>
              <span class="ivs-spoken">{{ copy.text }}: <strong>{{ data.name[group.language] || '—' }}</strong></span>
            </div>
            <div v-if="group.voices.length" class="ivs-cards">
              <ItemVoiceCard v-for="voice in group.voices" :key="voice.id" :voice="voice" :capabilities="capabilities"
                :selected="group.selected?.id === voice.id" :busy="busy" :optimizing="busyKey === `optimize:${voice.id}`"
                :active="activePlayer === voice.id" :revision="revision" @play="onPlay(voice.id)"
                @replace="openUpload(group.language, voice.id)" @drop="openUpload(group.language, voice.id, $event)"
                @optimize="optimize(voice)" @select="select(voice, $event)" />
            </div>
            <p v-else class="ivs-empty">{{ copy.empty }}</p>
            <button type="button" class="ivs-drop" :class="{ 'ivs-drop-active': dragLanguage === group.language }" :disabled="busy"
              @click="openUpload(group.language)" @dragover.prevent.stop="onLanguageDrag($event, group.language)"
              @dragleave="leaveLanguageDrop($event)" @drop.prevent.stop="dropInLanguage($event, group.language)">
              <i class="mdi mdi-upload" aria-hidden="true"></i><span><strong>{{ copy.dropAdd }} {{ langLabel(group.language) }}</strong><small>{{ copy.or }} <u>{{ copy.browse }}</u> · {{ formatsLabel }}</small></span>
            </button>
          </div>
        </section>
      </div>
      <p class="ivs-help"><i class="mdi mdi-information-outline" aria-hidden="true"></i>{{ copy.dropHint }}</p>
    </template>

    <VoiceDialog v-if="dialog === 'generate'" :title="copy.generate" :busy="!!busyKey" @close="closeDialog" @submit="generate">
      <div class="ivs-dialog-language"><i :class="`flag-icon flag-icon-${langFlag(formLanguage)}`" aria-hidden="true"></i><div><strong>{{ langLabel(formLanguage) }}</strong><small>{{ copy.newRecording }} {{ itemName }}</small></div></div>
      <p v-if="!capabilities.generate" class="ivs-notice">{{ copy.generationUnavailable }}</p>
      <div v-if="formError" class="ivs-notice ivs-error" role="alert">{{ formError }}<button v-if="presetsFailed" type="button" class="ivs-button" @click="loadPresets">{{ copy.retry }}</button></div>
      <label class="ivs-field">{{ copy.text }}<input v-model="generationText" required :disabled="!!busyKey || !capabilities.generate" /></label>
      <label class="ivs-field">{{ copy.provider }}<select :value="chosenPreset?.provider || ''" :disabled="!!busyKey || !availablePresets.length" @change="chooseProvider">
        <option v-if="!availablePresets.length" value="">{{ copy.currentConfig }}</option><option v-for="provider in providers" :key="provider" :value="provider">{{ provider }}</option>
      </select></label>
      <label class="ivs-field">{{ copy.baseVoice }}<select v-model="chosenPresetId" :disabled="!!busyKey || !availablePresets.length">
        <option v-if="!availablePresets.length" value="">{{ presetsLoading ? copy.loading : copy.noPresets }}</option>
        <option v-for="preset in providerPresets" :key="preset.id" :value="preset.id">{{ preset.name }}</option>
      </select></label>
      <label class="ivs-field">{{ copy.model }}<input :value="chosenPreset?.model || '—'" readonly /></label>
      <fieldset class="ivs-settings" :disabled="!!busyKey || !chosenPreset || !capabilities.generate"><legend>{{ copy.settings }}</legend>
        <label class="ivs-range"><span>{{ copy.speed }}<output>{{ speed.toFixed(1) }}×</output></span><input v-model.number="speed" :aria-label="copy.speed" type="range" min="0.5" max="2" step="0.1" /></label>
        <label v-if="chosenPreset?.stability !== undefined" class="ivs-range"><span>{{ copy.stability }}<output>{{ Math.round(stability * 100) }}%</output></span><input v-model.number="stability" :aria-label="copy.stability" type="range" min="0" max="1" step="0.01" /></label>
        <label v-if="chosenPreset?.similarity !== undefined" class="ivs-range"><span>{{ copy.similarity }}<output>{{ Math.round(similarity * 100) }}%</output></span><input v-model.number="similarity" :aria-label="copy.similarity" type="range" min="0" max="1" step="0.01" /></label>
        <label class="ivs-checkbox"><span>{{ copy.autoOptimize }}</span><input v-model="autoOptimize" type="checkbox" :disabled="!capabilities.optimize" role="switch" /></label>
      </fieldset>
      <p class="ivs-notice">{{ copy.keepSelection }}</p>
      <template #footer><button type="button" class="ivs-button" :disabled="!!busyKey" @click="closeDialog">{{ copy.cancel }}</button><button type="submit" class="ivs-button ivs-primary ivs-submit" :disabled="!canGenerate"><i class="mdi" :class="busyKey ? 'mdi-loading mdi-spin' : 'mdi-auto-fix'" aria-hidden="true"></i>{{ busyKey ? copy.generating : copy.generate }}</button></template>
    </VoiceDialog>

    <VoiceDialog v-if="dialog === 'upload'" :title="uploadMode === 'replace' ? copy.replace : copy.add" :busy="!!busyKey" @close="closeDialog" @submit="upload">
      <label class="ivs-field">{{ copy.language }}<select v-model="formLanguage" :disabled="!!busyKey"><option v-for="language in SUPPORTED_LANGUAGES" :key="language" :value="language">{{ langLabel(language) }}</option></select></label>
      <div class="ivs-mode" role="group" :aria-label="copy.mode"><button type="button" :aria-pressed="uploadMode === 'add'" :disabled="!!busyKey" @click="uploadMode = 'add'">{{ copy.addNew }}</button><button type="button" :aria-pressed="uploadMode === 'replace'" :disabled="!!busyKey || !uploadVoices.length" @click="uploadMode = 'replace'">{{ copy.replaceExisting }}</button></div>
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
      <template #footer><button type="button" class="ivs-button" :disabled="!!busyKey" @click="closeDialog">{{ copy.cancel }}</button><button type="submit" class="ivs-button ivs-primary ivs-submit" :disabled="!canUpload"><i class="mdi" :class="busyKey ? 'mdi-loading mdi-spin' : 'mdi-upload'" aria-hidden="true"></i>{{ busyKey ? copy.saving : uploadMode === 'replace' ? copy.replace : copy.upload }}</button></template>
    </VoiceDialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Voice, Language } from '../types/viewModels'
import { SUPPORTED_LANGUAGES } from '../types/viewModels'
import type { AudioVersion, ItemVoiceList, ItemVoiceRecord, VoicePreset } from '../types/itemVoices'
import { legacyVoiceCapabilities } from '../types/itemVoices'
import { generateItemVoice, listItemVoices, listVoicePresets, optimizeItemVoice, selectItemVoice, uploadItemVoice } from '../api/itemVoicesClient'
import { invalidateItemDetail } from '../api/cache'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'
import { selectedVoice, validateAudioFile, voiceErrorMessage, voiceTitle } from '../utils/itemVoices'
import { langFlag, langLabel } from '../utils/voiceLabels'
import ItemVoiceCard from './ItemVoiceCard.vue'
import VoiceDialog from './VoiceDialog.vue'
import VoiceAudioPreview from './VoiceAudioPreview.vue'

const props = defineProps<{ categoryId: string; itemId: string; itemName: string; voices: Voice[]; externalPlayingId: string | null; disabled: boolean }>()
const emit = defineEmits<{ changed: []; play: [] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
const mockMode = import.meta.env.VITE_MOCK_DATA === 'true'
const data = ref<ItemVoiceList | null>(null)
const loading = ref(false)
const loadFailed = ref(false)
const notice = ref<{ text: string; error: boolean } | null>(null)
const busyKey = ref('')
const busy = computed(() => !!busyKey.value || props.disabled || loading.value)
const capabilities = computed(() => ({ ...legacyVoiceCapabilities, ...data.value?.capabilities }))
const formats = computed(() => capabilities.value.upload_formats.length ? capabilities.value.upload_formats : ['mp3'])
const formatsLabel = computed(() => formats.value.map((format) => format.toUpperCase()).join(', '))
const acceptedFormats = computed(() => formats.value.map((format) => `.${format}`).join(','))
const expanded = ref(new Set<Language>([language.value]))
const activePlayer = ref<string | null>(null)
const revision = ref(0)
const groups = computed(() => SUPPORTED_LANGUAGES.map((language) => {
  const voices = (data.value?.voices || []).filter((voice) => voice.language === language && voice.file_uri)
  return { language, voices, selected: selectedVoice(voices, capabilities.value) }
}))
const voiceCount = computed(() => groups.value.reduce((count, group) => count + group.voices.length, 0))
const identity = computed(() => `${props.categoryId}/${props.itemId}`)
let loadController: AbortController | null = null
let presetController: AbortController | null = null
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
  presetController?.abort()
  busyKey.value = ''
  closeDialog()
  data.value = null
  notice.value = null
  activePlayer.value = null
  expanded.value = new Set([language.value])
  chosenPresets.value = {}
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
function baseVoiceLabel(language: Language): string {
  const preset = chosenPresets.value[language]
  if (preset) return `${preset.provider} / ${preset.name}`
  const voice = groups.value.find((group) => group.language === language)?.voices.find((voice) => voice.parameters?.actor || voice.parameters?.voice)
  return voice ? `${voice.provider} / ${voice.parameters?.actor || voice.parameters?.voice}` : copy.value.currentConfig
}

const dialog = ref<'upload' | 'generate' | null>(null)
const formLanguage = ref<Language>('en')
const formError = ref('')
function closeDialog(): void {
  if (busyKey.value) return
  presetController?.abort()
  dialog.value = null
  formError.value = ''
  activePlayer.value = null
  setFile(null)
}

async function mutate(key: string, action: () => Promise<unknown>, success: string): Promise<void> {
  if (busy.value) return
  const id = ++operation
  const categoryId = props.categoryId
  const itemId = props.itemId
  busyKey.value = key
  formError.value = ''
  notice.value = null
  activePlayer.value = null
  try {
    await action()
    invalidateItemDetail(categoryId, itemId)
    if (operation !== id) return
    revision.value++
    const refreshed = await load()
    if (operation !== id) return
    busyKey.value = ''
    closeDialog()
    notice.value = { text: refreshed ? success : copy.value.refreshError, error: !refreshed }
    emit('changed')
  } catch (error) {
    if (operation !== id) return
    const message = voiceErrorMessage(error) || copy.value.operationError
    if (dialog.value) formError.value = message
    else notice.value = { text: message, error: true }
  } finally {
    if (operation === id) busyKey.value = ''
  }
}
function optimize(voice: ItemVoiceRecord): void {
  if (!capabilities.value.optimize) return
  void mutate(`optimize:${voice.id}`, () => optimizeItemVoice(props.categoryId, props.itemId, voice.id), copy.value.optimizedSuccess)
}
function select(voice: ItemVoiceRecord, version: AudioVersion): void {
  if (!capabilities.value.select || (version === 'optimized' && !voice.optimized_file_uri)) return
  void mutate(`select:${voice.id}`, () => selectItemVoice(props.categoryId, props.itemId, voice.id, version), copy.value.selectedSuccess)
}

const availablePresets = ref<VoicePreset[]>([])
const chosenPresets = ref<Partial<Record<Language, VoicePreset>>>({})
const chosenPresetId = ref('')
const chosenPreset = computed(() => availablePresets.value.find((preset) => preset.id === chosenPresetId.value))
const providers = computed(() => [...new Set(availablePresets.value.map((preset) => preset.provider))])
const providerPresets = computed(() => availablePresets.value.filter((preset) => preset.provider === chosenPreset.value?.provider))
const presetsLoading = ref(false)
const presetsFailed = ref(false)
const generationText = ref('')
const speed = ref(1)
const stability = ref(.5)
const similarity = ref(.75)
const autoOptimize = ref(false)
const canGenerate = computed(() => !busy.value && capabilities.value.generate && !!chosenPreset.value && !!generationText.value.trim() && !presetsLoading.value)
watch(chosenPreset, (preset) => {
  if (!preset) return
  speed.value = preset.speed
  stability.value = preset.stability ?? .5
  similarity.value = preset.similarity ?? .75
  chosenPresets.value = { ...chosenPresets.value, [formLanguage.value]: preset }
})
function chooseProvider(event: Event): void {
  chosenPresetId.value = availablePresets.value.find((preset) => preset.provider === (event.target as HTMLSelectElement).value)?.id || ''
}
async function loadPresets(): Promise<void> {
  presetController?.abort()
  const controller = new AbortController()
  presetController = controller
  availablePresets.value = []
  chosenPresetId.value = ''
  presetsFailed.value = false
  formError.value = ''
  presetsLoading.value = false
  if (!capabilities.value.presets) return
  presetsLoading.value = true
  try {
    const result = await listVoicePresets(props.categoryId, props.itemId, formLanguage.value, controller.signal)
    if (controller.signal.aborted) return
    availablePresets.value = result.filter((preset) => preset.language === formLanguage.value)
    const previous = chosenPresets.value[formLanguage.value]?.id
    chosenPresetId.value = availablePresets.value.find((preset) => preset.id === previous)?.id || availablePresets.value[0]?.id || ''
  } catch {
    if (!controller.signal.aborted) { presetsFailed.value = true; formError.value = copy.value.presetsError }
  } finally {
    if (presetController === controller) presetsLoading.value = false
  }
}
function openGenerate(language: Language): void {
  if (busy.value) return
  activePlayer.value = null
  formLanguage.value = language
  generationText.value = data.value?.name[language] || ''
  autoOptimize.value = false
  dialog.value = 'generate'
  void loadPresets()
}
function generate(): void {
  if (!canGenerate.value || !chosenPreset.value) return
  const body = { language: formLanguage.value, text: generationText.value.trim(), preset_id: chosenPreset.value.id, speed: speed.value,
    stability: chosenPreset.value.stability === undefined ? undefined : stability.value,
    similarity: chosenPreset.value.similarity === undefined ? undefined : similarity.value,
    optimize: autoOptimize.value && capabilities.value.optimize }
  expanded.value = new Set([...expanded.value, formLanguage.value])
  void mutate('generate', () => generateItemVoice(props.categoryId, props.itemId, body), copy.value.generatedSuccess)
}

const uploadMode = ref<'add' | 'replace'>('add')
const uploadTarget = ref('')
const uploadFile = ref<File | null>(null)
const uploadUrl = ref('')
const uploadDragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const uploadVoices = computed(() => groups.value.find((group) => group.language === formLanguage.value)?.voices || [])
const cannotAdd = computed(() => uploadMode.value === 'add' && !capabilities.value.multiple_voices && uploadVoices.value.length > 0)
const canUpload = computed(() => !busy.value && !!uploadFile.value && !cannotAdd.value && (uploadMode.value === 'add' || uploadVoices.value.some((voice) => voice.id === uploadTarget.value)))
watch(formLanguage, () => {
  uploadTarget.value = uploadVoices.value[0]?.id || ''
  if (!uploadVoices.value.length) uploadMode.value = 'add'
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
  if (event.dataTransfer?.files.length) openUpload(language, undefined, event.dataTransfer.files)
}
void load()
onBeforeUnmount(() => {
  operation++
  loadController?.abort()
  presetController?.abort()
  if (uploadUrl.value) URL.revokeObjectURL(uploadUrl.value)
})
</script>

<style scoped>
.ivs { container-type: inline-size; container-name: voices; color: #111a3d; border: 1px solid #e6eaf4; border-radius: 10px; background: #fff; padding: 1.35rem; margin-bottom: 1rem; box-shadow: 0 4px 18px #19264d0a; }
.ivs-header { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.25rem; }
.ivs-header h2 { font-size: 1.45rem; font-weight: 700; margin: 0 0 .35rem; }
.ivs-header p { color: #63708c; font-size: .87rem; margin: 0; line-height: 1.5; }
.ivs-summary { display: flex; align-items: center; flex-wrap: wrap; gap: .8rem; font-size: .8rem; color: #63708c; }
.ivs-button { display: inline-flex; align-items: center; justify-content: center; gap: .4rem; border: 1px solid #818cf8; border-radius: 6px; background: white; color: #4f46e5; font-weight: 600; font-size: .8rem; padding: .5rem .85rem; min-height: 38px; cursor: pointer; line-height: 1.3; }
.ivs-button:hover:not(:disabled) { background: #eef2ff; }
.ivs-primary { background: #4f46e5; color: white; border-color: #4f46e5; }
.ivs-primary:hover:not(:disabled) { background: #4338ca; }
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
.ivs-group-actions { display: flex; gap: .55rem; }
.ivs-group-body { border-top: 1px solid #dbe6f4; padding: .9rem; }
.ivs-base { display: flex; flex-wrap: wrap; align-items: center; gap: .65rem; font-size: .78rem; margin-bottom: 1rem; }
.ivs-base > span:first-child { color: #596984; }
.ivs-base button { display: flex; justify-content: space-between; gap: 1.5rem; align-items: center; min-height: 36px; border: 1px solid #cbd8ec; border-radius: 6px; background: white; color: #334155; padding: .4rem .65rem; max-width: 100%; overflow-wrap: anywhere; }
.ivs-spoken { margin-left: .65rem; overflow-wrap: anywhere; }
.ivs-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: stretch; gap: .9rem; }
.ivs-drop { display: flex; width: 100%; align-items: center; justify-content: center; gap: .85rem; padding: 1rem; margin-top: 1rem; border: 1px dashed #93baf2; border-radius: 7px; background: #f5f9ff; color: #334155; cursor: pointer; }
.ivs-drop > i { color: #4f46e5; font-size: 1.5rem; }
.ivs-drop strong { display: block; font-size: .8rem; font-weight: 500; }
.ivs-drop small { display: block; color: #63708c; margin-top: .2rem; }
.ivs-drop u { color: #4f46e5; text-underline-offset: 3px; }
.ivs-drop-active { background: #e0e7ff !important; outline: 2px dashed #4f46e5; outline-offset: -4px; }
.ivs-help { display: flex; gap: .45rem; font-size: .76rem; color: #63708c; margin: 1rem 0 0; }
.ivs-notice { display: flex; align-items: center; justify-content: space-between; gap: .65rem; border-radius: 7px; background: #eff6ff; color: #475569; padding: .8rem 1rem; font-size: .8rem; line-height: 1.5; margin: 0 0 1rem; }
.ivs-notice:empty { display: none; }
.ivs-notice > button:not(.ivs-button), .ivs-file > button { flex: none; display: grid; place-items: center; min-width: 36px; min-height: 36px; border: 0; background: transparent; color: inherit; border-radius: 5px; font-size: 1.2rem; cursor: pointer; }
.ivs-error { background: #fff1f2; color: #9f1239; }
.ivs-availability { background: #f8fafc; border: 1px solid #e2e8f0; }
.ivs-demo { font-size: .74rem; justify-content: flex-start; }
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
.ivs-mode { display: flex; background: #edf3fc; border: 1px solid #cbd8ec; border-radius: 6px; margin-bottom: 1.1rem; }
.ivs-mode button { flex: 1; border: 0; border-radius: 5px; padding: .65rem; min-height: 44px; background: transparent; color: #536481; cursor: pointer; font-size: .85rem; }
.ivs-mode button[aria-pressed="true"] { background: #4f46e5; color: white; }
.ivs-mode button:disabled { opacity: .5; cursor: default; }
.ivs-upload-drop { display: flex; flex-direction: column; align-items: center; gap: .55rem; text-align: center; border: 1px dashed #93baf2; padding: 1.5rem; background: #f8fbff; border-radius: 8px; }
.ivs-upload-drop > i { color: #4f46e5; font-size: 2rem; }
.ivs-upload-drop > span, .ivs-upload-drop small { color: #64748b; font-size: .8rem; }
.ivs-file { display: flex; align-items: center; gap: .8rem; padding: .85rem; border-radius: 7px; background: #f1f7ff; margin-top: .8rem; }
.ivs-file > i { font-size: 1.6rem; }
.ivs-file > div { flex: 1; min-width: 0; font-size: .85rem; overflow-wrap: anywhere; }
.ivs-upload-preview { margin: 1.2rem 0; font-size: .85rem; }
.ivs-upload-preview > strong { display: block; margin-bottom: .6rem; }
button:focus-visible, input:focus-visible, select:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 2px; }
@container voices (max-width: 65rem) {
  .ivs-current { display: none; }
  .ivs-toggle { flex-basis: 15rem; }
  .ivs-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@container voices (max-width: 42rem) {
  .ivs-cards { grid-template-columns: minmax(0, 1fr); }
  .ivs-group-actions { width: 100%; }
  .ivs-group-actions > button { flex: 1; }
  .ivs-group-body { padding: .65rem; }
  .ivs-spoken { width: 100%; margin: 0; }
  .ivs-summary { width: 100%; justify-content: space-between; }
}
@media (max-width: 575px) {
  .ivs { padding: .8rem; }
  .ivs-header { gap: .8rem; }
  .ivs-header h2 { font-size: 1.3rem; }
  .ivs-header p { font-size: .8rem; }
  .ivs-toggle { min-height: 44px; }
  .ivs-button { min-height: 44px; padding: .5rem .6rem; font-size: .76rem; }
  .ivs-base button { min-height: 44px; }
  .ivs-group-header { padding: .5rem .6rem; }
  .ivs-language { font-size: .92rem; }
  .ivs-toggle { gap: .5rem; }
  .ivs-notice { padding: .7rem; }
  .ivs-file > button, .ivs-notice > button:not(.ivs-button) { min-height: 44px; min-width: 44px; }
  .ivs-field input, .ivs-field select { font-size: 1rem; }
}
</style>
