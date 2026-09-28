<template>
  <article class="iv-card" :class="{ 'iv-card-selected': selected, 'iv-card-drag': dragging }"
    :aria-label="title" @dragover.prevent.stop="onDragOver" @dragleave="onDragLeave" @drop.prevent.stop="onDrop">
    <header class="iv-card-header">
      <div class="iv-card-heading">
        <div class="iv-card-title"><h3>{{ title }}</h3><span v-if="selected" class="iv-badge">{{ copy.inUse }}</span></div>
        <p>{{ source === 'uploaded' ? copy.uploaded : voice.provider || copy.unknown }}<span v-if="source === 'generated'"> · {{ copy.generated }}</span></p>
      </div>
      <button type="button" class="iv-replace" :disabled="busy" :aria-label="`${copy.replace}: ${title}`" @click="$emit('replace')">
        <i class="mdi mdi-pencil" aria-hidden="true"></i><span>{{ copy.replace }}</span>
      </button>
    </header>

    <div v-if="source === 'generated' || voice.optimized_file_uri" class="iv-versions" :aria-label="`${title}: ${copy.preview}`" role="group">
      <button type="button" :aria-pressed="version === 'original'" @click="version = 'original'">{{ copy.original }}</button>
      <button type="button" :aria-pressed="version === 'optimized'" :disabled="!voice.optimized_file_uri" :title="!voice.optimized_file_uri ? copy.noOptimized : undefined" @click="version = 'optimized'">{{ copy.optimized }}</button>
    </div>
    <VoiceAudioPreview :src="src" :label="`${title} · ${copy[version]}`" :active="active" @play="$emit('play')" />

    <dl v-if="source === 'uploaded'" class="iv-meta">
      <div><dt>{{ copy.file }}</dt><dd>{{ voice.parameters?.filename || audioFileName(voice.file_uri) }}</dd></div>
      <div><dt>{{ copy.source }}</dt><dd>{{ copy.uploaded }}</dd></div>
      <div v-if="created"><dt>{{ copy.created }}</dt><dd>{{ created }}</dd></div>
    </dl>
    <details v-else class="iv-details" :open="selected">
      <summary>{{ copy.details }}</summary>
      <dl v-if="metadata.length" class="iv-meta"><div v-for="entry in metadata" :key="entry.label"><dt>{{ entry.label }}</dt><dd>{{ entry.value }}</dd></div></dl>
      <p v-else class="iv-empty-meta">{{ copy.noDetails }}</p>
      <VoiceParametersTooltip v-if="Object.keys(voice.parameters || {}).length" class="iv-all-parameters" :parameters="voice.parameters" :provider="voice.provider">{{ copy.settings }} <i class="mdi mdi-information-outline" aria-hidden="true"></i></VoiceParametersTooltip>
    </details>

    <div class="iv-card-footer">
      <button v-if="source === 'generated'" type="button" class="iv-button" :disabled="busy || !capabilities.optimize"
        :title="!capabilities.optimize ? copy.optimizeUnavailable : undefined" @click="$emit('optimize')">
        <i :class="optimizing ? 'mdi mdi-loading mdi-spin' : 'mdi mdi-auto-fix'" aria-hidden="true"></i>
        {{ optimizing ? copy.optimizing : voice.optimized_file_uri ? copy.optimizeAgain : copy.optimize }}
      </button>
      <button type="button" class="iv-button iv-select" :class="{ 'iv-is-selected': selectedVersion }"
        :disabled="busy || selectedVersion || !capabilities.select" :title="!selectedVersion && !capabilities.select ? copy.selectUnavailable : undefined"
        @click="$emit('select', version)">
        <i class="mdi mdi-check" aria-hidden="true"></i>{{ selectedVersion ? `${copy.selected} ${langLabel(voice.language)}` : copy.use }}
      </button>
    </div>
    <button type="button" class="iv-drop" :disabled="busy" @click="$emit('replace')"><i class="mdi mdi-upload" aria-hidden="true"></i>{{ copy.dropReplace }}</button>
  </article>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AudioVersion, ItemVoiceRecord, VoiceCapabilities } from '../types/itemVoices'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'
import { audioFileName, voiceTitle, voiceSource, voiceAudioUri } from '../utils/itemVoices'
import { langLabel } from '../utils/voiceLabels'
import { cacheBustedUri } from '../utils/cacheBustedUri'
import VoiceAudioPreview from './VoiceAudioPreview.vue'
import VoiceParametersTooltip from './VoiceParametersTooltip.vue'

const props = defineProps<{ voice: ItemVoiceRecord; capabilities: VoiceCapabilities; selected: boolean; busy: boolean; optimizing: boolean; active: boolean; revision: number }>()
const emit = defineEmits<{ play: []; replace: []; drop: [files: FileList]; optimize: []; select: [version: AudioVersion] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
const title = computed(() => voiceTitle(props.voice))
const source = computed(() => voiceSource(props.voice))
const version = ref<AudioVersion>(props.voice.selected_version === 'optimized' && props.voice.optimized_file_uri ? 'optimized' : 'original')
const src = computed(() => {
  const uri = voiceAudioUri(props.voice, version.value)
  return uri && !uri.startsWith('blob:') && !uri.startsWith('data:') ? cacheBustedUri(uri, props.revision) : uri
})
const selectedVersion = computed(() => props.selected && version.value === (props.voice.selected_version || 'original'))
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
.iv-card-heading p { margin: .3rem 0 0; color: #63708c; font-size: .78rem; overflow-wrap: anywhere; }
.iv-badge { color: white; background: #2f8f68; border-radius: 20px; padding: .2rem .55rem; font-size: .68rem; white-space: nowrap; }
.iv-replace { display: flex; align-items: center; gap: .35rem; border: 0; border-radius: 5px; background: transparent; color: #4f46e5; font-size: .72rem; min-height: 32px; cursor: pointer; }
.iv-replace span { display: inline; }
.iv-replace i { font-size: 1.1rem; }
.iv-versions { display: flex; border: 1px solid #d7e3f3; border-radius: 6px; overflow: hidden; background: #eef3fa; }
.iv-versions button { flex: 1; border: 0; background: transparent; padding: .45rem; color: #536481; font-size: .78rem; cursor: pointer; }
.iv-versions button[aria-pressed="true"] { background: #4f46e5; color: white; border-radius: 5px; }
.iv-card-selected .iv-versions button[aria-pressed="true"] { background: #2f8f68; }
.iv-versions button:disabled { opacity: .5; cursor: default; }
.iv-details { font-size: .78rem; }
.iv-details summary { cursor: pointer; color: #334155; padding: .2rem 0; }
.iv-meta { margin: .35rem 0 0; font-size: .77rem; display: grid; gap: .4rem; }
.iv-meta > div { display: grid; grid-template-columns: minmax(4rem, .8fr) minmax(0, 1.4fr); gap: .5rem; align-items: baseline; }
.iv-meta dt { font-weight: 400; color: #596984; }
.iv-meta dd { margin: 0; overflow-wrap: anywhere; }
.iv-all-parameters { margin-top: .65rem; color: #4f46e5; }
.iv-all-parameters :deep(.vpt-box) { left: 0; transform: none; min-width: 220px; max-width: min(320px, calc(100vw - 100px)); }
.iv-empty-meta { color: #64748b; margin: .5rem 0; }
.iv-card-footer { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: auto; padding-top: .35rem; }
.iv-button { display: inline-flex; justify-content: center; align-items: center; gap: .35rem; border: 1px solid #818cf8; border-radius: 6px; background: white; color: #4f46e5; font-size: .77rem; font-weight: 600; min-height: 36px; padding: .45rem .65rem; flex: 1 1 auto; cursor: pointer; }
.iv-select { background: #4f46e5; color: white; }
.iv-is-selected, .iv-is-selected:disabled { background: #e1f2eb; border-color: #e1f2eb; color: #24704f; opacity: 1; }
.iv-button:disabled:not(.iv-is-selected), .iv-replace:disabled { opacity: .5; cursor: default; }
.iv-drop { display: flex; justify-content: center; gap: .5rem; align-items: center; color: #596984; border: 1px dashed #a5b4fc; border-radius: 6px; background: transparent; min-height: 36px; font-size: .75rem; cursor: pointer; }
button:focus-visible, summary:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 2px; }
@media (max-width: 575px) {
  .iv-replace span { display: none; }
  .iv-card { padding: .8rem; gap: .8rem; }
  .iv-card-selected { padding: calc(.8rem - 1px); }
  .iv-replace { min-height: 44px; min-width: 44px; justify-content: center; }
  .iv-button, .iv-drop, .iv-versions button { min-height: 44px; }
  .iv-details summary { padding: .5rem 0; }
}
</style>
