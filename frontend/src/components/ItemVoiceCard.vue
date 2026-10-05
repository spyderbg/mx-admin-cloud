<template>
  <article class="iv-card" :class="{ 'iv-card-selected': selected, 'iv-card-drag': dragging }"
    :aria-label="title" @dragover.prevent.stop="onDragOver" @dragleave="onDragLeave" @drop.prevent.stop="onDrop">
    <header class="iv-card-header">
      <div class="iv-card-heading">
        <div class="iv-card-title"><h3>{{ title }}</h3><span v-if="selected" class="iv-badge">{{ copy.inUse }}</span></div>
      </div>
      <div class="iv-card-actions">
        <button type="button" class="iv-card-action iv-card-action-edit" :disabled="busy" :title="copy.replace" :aria-label="`${copy.replace}: ${title}`" @click="$emit('replace')">
          <i class="mdi mdi-pencil" aria-hidden="true"></i>
        </button>
        <button type="button" class="iv-card-action iv-card-action-delete" :disabled="busy" :title="copy.deleteVoice" :aria-label="`${copy.deleteVoice}: ${title}`" @click="$emit('delete')">
          <i class="mdi mdi-delete" aria-hidden="true"></i>
        </button>
      </div>
    </header>

    <dl class="iv-card-meta">
      <div v-if="generationText" class="iv-description-field iv-spoken-text">
        <dt>{{ copy.text }}:</dt>
        <dd><strong>{{ generationText }}</strong></dd>
      </div>
      <div class="iv-description-field iv-provider">
        <dt>{{ copy.provider }}:</dt>
        <dd>
          <VoiceParametersTooltip v-if="source === 'generated'" class="iv-provider-settings" :parameters="voice.parameters" :provider="voice.provider"
            :open="informationPopup === 'provider'" @mouseenter="informationPopup = 'provider'" @focusin="informationPopup = 'provider'"
            @mouseleave="closeProviderPopup" @focusout="closeProviderPopup" @keydown.esc.prevent.stop="informationPopup = null">
            <strong>{{ voice.provider || copy.unknown }}</strong>
          </VoiceParametersTooltip>
          <strong v-else>{{ voice.provider || copy.unknown }}</strong>
        </dd>
      </div>
      <VoiceGenerationDetails v-if="source !== 'uploaded'" class="iv-description-field" :parameters="voice.parameters" :metadata="metadata"
        :open="informationPopup === 'generation'" @update:open="updateGenerationPopup" />
    </dl>

    <dl v-if="source === 'uploaded'" class="iv-meta">
      <div><dt>{{ copy.file }}</dt><dd>{{ voice.parameters?.filename || audioFileName(voice.file_uri) }}</dd></div>
      <div><dt>{{ copy.source }}</dt><dd>{{ copy.uploaded }}</dd></div>
      <div v-if="created"><dt>{{ copy.created }}</dt><dd>{{ created }}</dd></div>
    </dl>
    <div v-if="source === 'generated' || voice.optimized_file_uri" class="iv-versions" :aria-label="`${title}: ${copy.preview}`" role="group">
      <button type="button" :aria-pressed="version === 'original'" @click="version = 'original'">{{ copy.original }}</button>
      <button type="button" :aria-pressed="version === 'optimized'" :disabled="!voice.optimized_file_uri" :title="!voice.optimized_file_uri ? copy.noOptimized : undefined" @click="version = 'optimized'">{{ copy.optimized }}</button>
    </div>
    <VoiceAudioPreview :src="src" :label="`${title} · ${copy[version]}`" :active="active" @play="$emit('play')" />

    <div class="iv-card-footer">
      <button v-if="source === 'generated'" type="button" class="iv-button" :disabled="busy || !capabilities.optimize"
        :title="!capabilities.optimize ? copy.optimizeUnavailable : undefined" @click="$emit('optimize')">
        <i :class="optimizing ? 'mdi mdi-loading mdi-spin' : 'mdi mdi-auto-fix'" aria-hidden="true"></i>
        {{ optimizing ? copy.optimizing : voice.optimized_file_uri ? copy.optimizeAgain : copy.optimize }}
      </button>
      <button type="button" class="iv-button iv-select" :class="{ 'iv-is-selected': selectedVersion }"
        :disabled="busy || selectedVersion || !canSelect" :title="!selectedVersion && !canSelect ? copy.selectUnavailable : undefined"
        @click="$emit('select', version)">
        <i class="mdi mdi-check" aria-hidden="true"></i>{{ selectedVersion ? `${copy.selected} ${langLabel(voice.language, language)}` : copy.use }}
      </button>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AudioVersion, ItemVoiceRecord, VoiceCapabilities } from '../types/itemVoices'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'
import { audioFileName, voiceTitle, voiceSource, voiceAudioUri, voiceSelectionId, voiceGenerationText } from '../utils/itemVoices'
import { langLabel } from '../utils/voiceLabels'
import { cacheBustedUri } from '../utils/cacheBustedUri'
import VoiceAudioPreview from './VoiceAudioPreview.vue'
import VoiceParametersTooltip from './VoiceParametersTooltip.vue'
import VoiceGenerationDetails from './VoiceGenerationDetails.vue'

const props = defineProps<{ voice: ItemVoiceRecord; capabilities: VoiceCapabilities; selected: boolean; busy: boolean; optimizing: boolean; active: boolean; revision: number }>()
const emit = defineEmits<{ play: []; replace: []; delete: []; drop: [files: FileList]; optimize: []; select: [version: AudioVersion] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
const title = computed(() => voiceTitle(props.voice))
const source = computed(() => voiceSource(props.voice))
const generationText = computed(() => source.value === 'generated' ? voiceGenerationText(props.voice) : '')
const version = ref<AudioVersion>(props.voice.selected_version === 'optimized' && props.voice.optimized_file_uri ? 'optimized' : 'original')
const src = computed(() => {
  const uri = voiceAudioUri(props.voice, version.value)
  return uri && !uri.startsWith('blob:') && !uri.startsWith('data:') ? cacheBustedUri(uri, props.revision) : uri
})
const selectedVersion = computed(() => props.selected && version.value === (props.voice.selected_version || 'original'))
const canSelect = computed(() => props.capabilities.select && !!voiceSelectionId(props.voice, version.value))
const informationPopup = ref<'provider' | 'generation' | null>(null)
function closeProviderPopup(event: MouseEvent | FocusEvent): void {
  if (informationPopup.value !== 'provider') return
  const target = event.currentTarget as HTMLElement
  if (event.relatedTarget instanceof Node && target.contains(event.relatedTarget)) return
  if (event.type === 'mouseleave' && target.contains(document.activeElement)) return
  informationPopup.value = null
}
function updateGenerationPopup(open: boolean): void {
  if (open) informationPopup.value = 'generation'
  else if (informationPopup.value === 'generation') informationPopup.value = null
}
watch(() => props.voice.optimized_file_uri, (uri) => { if (!uri && version.value === 'optimized') version.value = 'original' })
const created = computed(() => {
  const date = props.voice.parameters?.date
  if (typeof date !== 'string' || !Number.isFinite(Date.parse(date))) return ''
  return new Intl.DateTimeFormat(language.value, { dateStyle: 'medium' }).format(new Date(date))
})
const metadata = computed(() => {
  const p = props.voice.parameters || {}
  const settings = p.voice_settings && typeof p.voice_settings === 'object' ? p.voice_settings as Record<string, unknown> : {}
  const rows: { label: string; value: string }[] = []
  if (typeof p.model === 'string') rows.push({ label: copy.value.model, value: p.model })
  if (typeof p.speed === 'number') rows.push({ label: copy.value.speed, value: `${p.speed}×` })
  if (typeof settings.stability === 'number') rows.push({ label: copy.value.stability, value: `${Math.round(settings.stability * 100)}%` })
  if (typeof settings.similarity_boost === 'number') rows.push({ label: copy.value.similarity, value: `${Math.round(settings.similarity_boost * 100)}%` })
  if (created.value) rows.push({ label: copy.value.created, value: created.value })
  return rows
})
const dragging = ref(false)
function onDragOver(event: DragEvent): void {
  if (props.busy || !event.dataTransfer?.types.includes('Files')) return
  event.dataTransfer.dropEffect = 'copy'
  dragging.value = true
}
function onDragLeave(event: DragEvent): void {
  if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node | null)) dragging.value = false
}
function onDrop(event: DragEvent): void {
  dragging.value = false
  if (!props.busy && event.dataTransfer?.files.length) emit('drop', event.dataTransfer.files)
}
</script>

<style scoped>
.iv-card { min-width: 0; display: flex; flex-direction: column; gap: .95rem; padding: 1rem; border: 1px solid #b8d4f0; border-radius: 10px; background: #f8fbff; }
.iv-card-selected { border: 2px solid #2f8f68; padding: calc(1rem - 1px); }
.iv-card-drag { background: #e0e7ff; outline: 2px dashed #4f46e5; outline-offset: -5px; }
.iv-card-header { display: flex; align-items: flex-start; gap: .4rem; }
.iv-card-heading { min-width: 0; flex: 1; }
.iv-card-title { display: flex; flex-wrap: wrap; gap: .45rem; align-items: center; }
.iv-card-title h3 { font-size: 1rem; font-weight: 700; margin: 0; overflow-wrap: anywhere; }
.iv-badge { color: white; background: #2f8f68; border-radius: 20px; padding: .2rem .55rem; font-size: .68rem; white-space: nowrap; }
.iv-card-meta { position: relative; min-width: 0; display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr); grid-template-rows: auto auto; row-gap: .3rem; margin: 0; padding: 0; border: 1px solid #d7e3f3; border-radius: 6px; background: #fff; }
.iv-description-field { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr); grid-template-rows: subgrid; grid-row: span 2; align-items: start; row-gap: inherit; margin: 0; padding: .55rem .6rem; color: #596984; font-size: .75rem; line-height: 1.4; }
.iv-description-field + .iv-description-field { border-left: 1px solid #e5edf8; }
.iv-description-field > :deep(dt) { min-width: 0; font-weight: 400; overflow-wrap: anywhere; }
.iv-description-field > :deep(dd) { min-width: 0; margin: 0; }
.iv-description-field strong, .iv-description-field :deep(.vgd-trigger) { color: #111a3d; font-size: .82rem; font-weight: 700; overflow-wrap: anywhere; }
.iv-card-actions { display: flex; gap: .35rem; flex: none; }
.iv-card-action { display: inline-flex; align-items: center; justify-content: center; width: 1.75rem; height: 1.75rem; padding: 0; border: 0; border-radius: 50%; color: #fff; font-size: .9rem; cursor: pointer; }
.iv-card-action-edit { background: #2563eb; }
.iv-card-action-edit:hover:not(:disabled) { background: #1d4ed8; }
.iv-card-action-delete { background: #ef4444; }
.iv-card-action-delete:hover:not(:disabled) { background: #dc2626; }
.iv-versions { display: flex; border: 1px solid #d7e3f3; border-radius: 6px; overflow: hidden; background: #eef3fa; }
.iv-versions button { flex: 1; border: 0; background: transparent; padding: .45rem; color: #536481; font-size: .78rem; cursor: pointer; }
.iv-versions button[aria-pressed="true"] { background: #4f46e5; color: white; border-radius: 5px; }
.iv-card-selected .iv-versions button[aria-pressed="true"] { background: #2f8f68; }
.iv-versions button:disabled { opacity: .5; cursor: default; }
.iv-meta { margin: .35rem 0 0; font-size: .77rem; display: grid; gap: .4rem; }
.iv-meta > div { display: grid; grid-template-columns: minmax(4rem, .8fr) minmax(0, 1.4fr); gap: .5rem; align-items: baseline; }
.iv-meta dt { font-weight: 400; color: #596984; }
.iv-meta dd { margin: 0; overflow-wrap: anywhere; }
.iv-provider-settings { position: static; }
.iv-provider-settings :deep(.vpt-box) { bottom: calc(100% + 8px); left: 0; transform: none; min-width: 220px; max-width: min(320px, calc(100vw - 100px)); }
.iv-card-footer { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: auto; padding-top: .35rem; }
.iv-button { display: inline-flex; justify-content: center; align-items: center; gap: .35rem; border: 1px solid #818cf8; border-radius: 6px; background: white; color: #4f46e5; font-size: .77rem; font-weight: 600; min-height: 36px; padding: .45rem .65rem; flex: 1 1 auto; cursor: pointer; }
.iv-select { background: #4f46e5; color: white; }
.iv-is-selected, .iv-is-selected:disabled { background: #e1f2eb; border-color: #e1f2eb; color: #24704f; opacity: 1; }
.iv-button:disabled:not(.iv-is-selected), .iv-card-action:disabled { opacity: .5; cursor: default; }
button:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 2px; }
@media (max-width: 575px) {
  .iv-card { padding: .8rem; gap: .8rem; }
  .iv-card-selected { padding: calc(.8rem - 1px); }
  .iv-card-meta { grid-auto-flow: row; grid-template-columns: minmax(0, 1fr); grid-template-rows: none; row-gap: .2rem; }
  .iv-description-field + .iv-description-field { border-left: 0; border-top: 1px solid #e5edf8; }
  .iv-card-action { width: 2.75rem; height: 2.75rem; }
  .iv-button, .iv-versions button { min-height: 44px; }
}
</style>
