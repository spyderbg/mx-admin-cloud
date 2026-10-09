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
        <dd>
          <JsonDetailsPopup :title="copy.text" :data="{ text: generationText }" :open="informationPopup === 'text'" :top-inset="72"
            :copy-label="copy.copyJson" :copied-label="copy.copied" :copy-error-label="copy.copyError" :empty-label="copy.noDetails"
            @update:open="updateInformationPopup('text', $event)">{{ generationText }}</JsonDetailsPopup>
        </dd>
      </div>
      <div class="iv-description-field iv-provider">
        <dt>{{ copy.provider }}:</dt>
        <dd>
          <VoiceParametersTooltip :parameters="voice.parameters" :provider="voice.provider"
            :open="informationPopup === 'provider'" @update:open="updateInformationPopup('provider', $event)">
            {{ voice.provider || copy.unknown }}
          </VoiceParametersTooltip>
        </dd>
      </div>
      <VoiceGenerationDetails v-if="source !== 'uploaded'" class="iv-description-field" :parameters="voice.parameters"
        :open="informationPopup === 'generation'" @update:open="updateInformationPopup('generation', $event)" />
    </dl>

    <dl v-if="source === 'uploaded'" class="iv-meta">
      <div><dt>{{ copy.file }}</dt><dd>{{ voice.parameters?.filename || audioFileName(voice.file_uri) }}</dd></div>
      <div><dt>{{ copy.source }}</dt><dd>{{ copy.uploaded }}</dd></div>
      <div v-if="created"><dt>{{ copy.created }}</dt><dd>{{ created }}</dd></div>
    </dl>

    <section class="iv-panel" :class="panelClass('original')" :aria-label="`${title}: ${copy.original}`"
      @pointerdown="version = 'original'" @focusin="version = 'original'">
      <div class="iv-panel-head">
        <h4>{{ copy.original }}</h4>
        <span class="iv-chip">{{ fileInfo(voice.file_uri) }}</span>
      </div>
      <VoiceAudioPreview :src="bust(voice.file_uri)" :label="`${title} · ${copy.original}`" :active="active && playingKey === 'original'"
        @play="onPlay('original')" @duration="durations[voice.file_uri || ''] = $event" />
    </section>

    <section v-if="source === 'generated' || versions.length" class="iv-panel" :class="panelClass('optimized')"
      :aria-label="`${title}: ${copy.optimized}`" @pointerdown="version = 'optimized'" @focusin="version = 'optimized'">
      <div class="iv-panel-head">
        <h4>{{ copy.optimized }}</h4>
        <span v-if="!versions.length" class="iv-chip iv-chip-muted">{{ copy.noOptimizedFiles }}</span>
        <span v-else class="iv-chip">{{ fileInfo(current?.file_uri) }}</span>
      </div>
      <p v-if="current?.optimizer" class="iv-optimizer"><i class="mdi mdi-auto-fix" aria-hidden="true"></i>{{ current.optimizer }}</p>

      <div v-if="!versions.length" class="iv-empty-optimized">
        <p><i class="mdi mdi-information" aria-hidden="true"></i><span>{{ copy.optimizedHint }}</span></p>
        <p v-if="source === 'generated' && !capabilities.optimize" class="iv-warning" role="status">
          <i class="mdi mdi-alert-circle-outline" aria-hidden="true"></i><span>{{ copy.optimizeUnavailable }}</span>
        </p>
      </div>
      <div v-else class="iv-carousel" role="group" aria-roledescription="carousel" :aria-label="`${title}: ${copy.optimized}`">
        <div ref="track" class="iv-track" @scroll.passive="onTrackScroll">
          <div v-for="(entry, index) in versions" :key="entry.id || entry.file_uri" class="iv-slide" role="group" aria-roledescription="slide"
            :aria-label="position(index)">
            <VoiceAudioPreview :src="bust(entry.file_uri)" :label="`${title} · ${position(index)}`"
              :active="active && playingKey === `optimized:${index}`" @play="onPlay(`optimized:${index}`)" @duration="durations[entry.file_uri] = $event" />
          </div>
        </div>
        <div v-if="versions.length > 1" class="iv-dots">
          <button v-for="(entry, index) in versions" :key="entry.id || entry.file_uri" type="button" class="iv-dot"
            :class="{ 'iv-dot-current': index === optimizedIndex }" :aria-label="position(index)" :aria-current="index === optimizedIndex || undefined"
            @click="goTo(index)"></button>
        </div>
      </div>

      <button v-if="source === 'generated' && !versions.length && capabilities.optimize" type="button" class="iv-button iv-optimize" :disabled="busy"
        @click="$emit('optimize')">
        <i :class="optimizing ? 'mdi mdi-loading mdi-spin' : 'mdi mdi-auto-fix'" aria-hidden="true"></i>
        {{ optimizing ? copy.optimizing : copy.optimize }}
      </button>
    </section>

    <div class="iv-card-footer">
      <button type="button" class="iv-button iv-select" :class="{ 'iv-is-selected': selectedVersion }"
        :disabled="busy || selectedVersion || !canSelect" :title="!selectedVersion && !canSelect ? copy.selectUnavailable : undefined"
        @click="select">
        <i class="mdi mdi-check" aria-hidden="true"></i>{{ selectedVersion ? `${copy.selected} ${langLabel(voice.language, language)}` : copy.use }}
      </button>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { AudioVersion, ItemVoiceRecord, VoiceCapabilities } from '../types/itemVoices'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'
import { audioBitrateKbps, audioFileName, fetchAudioSize, formatFileSize, optimizedVersions, voiceTitle, voiceSource, voiceSelectionId, voiceGenerationText } from '../utils/itemVoices'
import { langLabel } from '../utils/voiceLabels'
import { cacheBustedUri } from '../utils/cacheBustedUri'
import VoiceAudioPreview from './VoiceAudioPreview.vue'
import VoiceParametersTooltip from './VoiceParametersTooltip.vue'
import VoiceGenerationDetails from './VoiceGenerationDetails.vue'
import JsonDetailsPopup from './JsonDetailsPopup.vue'

const props = defineProps<{ voice: ItemVoiceRecord; capabilities: VoiceCapabilities; selected: boolean; busy: boolean; optimizing: boolean; active: boolean; revision: number }>()
const emit = defineEmits<{ play: []; replace: []; delete: []; drop: [files: FileList]; optimize: []; select: [voiceId: string] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
const title = computed(() => voiceTitle(props.voice))
const source = computed(() => voiceSource(props.voice))
const generationText = computed(() => source.value === 'generated' ? voiceGenerationText(props.voice) : '')
const versions = computed(() => optimizedVersions(props.voice))
// Which rendition "Use for item" applies to, and which optimized file the carousel shows.
const version = ref<AudioVersion>(props.voice.selected_version === 'optimized' && versions.value.length ? 'optimized' : 'original')
const selectedOptimizedId = computed(() => props.selected && props.voice.selected_version === 'optimized'
  ? props.voice.selected_optimized_id ?? props.voice.optimized_voice_id ?? '' : null)
const optimizedIndex = ref(Math.max(0, versions.value.findIndex((entry) => entry.id === selectedOptimizedId.value)))
const current = computed(() => versions.value[optimizedIndex.value])
const selectedOriginal = computed(() => props.selected && (props.voice.selected_version || 'original') === 'original')
const optimizedSelected = computed(() => !!current.value && selectedOptimizedId.value !== null && current.value.id === selectedOptimizedId.value)
const selectedVersion = computed(() => version.value === 'original' ? selectedOriginal.value : optimizedSelected.value)
const selectionId = computed(() => voiceSelectionId(props.voice, version.value, current.value?.id))
const canSelect = computed(() => props.capabilities.select && !!selectionId.value)
const bust = (uri?: string | null): string =>
  uri && !uri.startsWith('blob:') && !uri.startsWith('data:') ? cacheBustedUri(uri, props.revision) : uri || ''
const audioFormat = (uri?: string | null): string => audioFileName(uri).split('.').pop()?.toUpperCase() || ''
// File size comes from the server, playing time from the player; bitrate is derived from both.
const sizes = reactive<Record<string, number>>({})
const durations = reactive<Record<string, number>>({})
watch(() => [props.voice.file_uri, ...versions.value.map((entry) => entry.file_uri), props.revision], () => {
  for (const uri of [props.voice.file_uri, ...versions.value.map((entry) => entry.file_uri)]) {
    if (!uri) continue
    const stamp = props.revision
    void fetchAudioSize(bust(uri)).then((size) => { if (size && stamp === props.revision) sizes[uri] = size })
  }
}, { immediate: true })
function fileInfo(uri?: string | null): string {
  if (!uri) return ''
  const bitrate = sizes[uri] && durations[uri] ? audioBitrateKbps(sizes[uri], durations[uri]) : null
  return [audioFormat(uri), bitrate && `${bitrate} kbps`, sizes[uri] && formatFileSize(sizes[uri])].filter(Boolean).join(' · ')
}
const position = (index: number): string => copy.value.optimizedPosition.replace('{n}', String(index + 1)).replace('{total}', String(versions.value.length))
function panelClass(panel: AudioVersion): Record<string, boolean> {
  return { 'iv-panel-active': version.value === panel, 'iv-panel-selected': panel === 'original' ? selectedOriginal.value : optimizedSelected.value }
}
function select(): void { if (selectionId.value) emit('select', selectionId.value) }

const playingKey = ref<string | null>(null)
function onPlay(key: string): void {
  playingKey.value = key
  version.value = key === 'original' ? 'original' : 'optimized'
  emit('play')
}
const track = ref<HTMLElement | null>(null)
function onTrackScroll(): void {
  const el = track.value
  if (!el?.clientWidth) return
  const index = Math.min(versions.value.length - 1, Math.round(el.scrollLeft / el.clientWidth))
  if (index === optimizedIndex.value) return
  optimizedIndex.value = index
  if (playingKey.value?.startsWith('optimized:')) playingKey.value = null
}
function goTo(index: number, behavior: ScrollBehavior = 'smooth'): void {
  const el = track.value
  if (!el || index < 0 || index >= versions.value.length) return
  version.value = 'optimized'
  el.scrollTo({ left: index * el.clientWidth, behavior })
}
// The carousel mounts once the first optimized file exists; open it on the selected one.
watch(track, (el) => { if (el) el.scrollLeft = optimizedIndex.value * el.clientWidth }, { flush: 'post' })
watch(versions, (list) => {
  if (!list.length && version.value === 'optimized') version.value = 'original'
  if (optimizedIndex.value >= list.length) optimizedIndex.value = Math.max(0, list.length - 1)
})
type InformationPopup = 'text' | 'provider' | 'generation'
const informationPopup = ref<InformationPopup | null>(null)
function updateInformationPopup(popup: InformationPopup, open: boolean): void {
  if (open) informationPopup.value = popup
  else if (informationPopup.value === popup) informationPopup.value = null
}
const created = computed(() => {
  const date = props.voice.parameters?.date
  if (typeof date !== 'string' || !Number.isFinite(Date.parse(date))) return ''
  return new Intl.DateTimeFormat(language.value, { dateStyle: 'medium' }).format(new Date(date))
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
.iv-description-field { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr); grid-template-rows: subgrid; grid-row: span 2; align-items: start; row-gap: inherit; margin: 0; padding: .55rem .45rem; color: #596984; font-size: .75rem; line-height: 1.4; }
.iv-description-field + .iv-description-field { border-left: 1px solid #e5edf8; }
.iv-description-field > :deep(dt) { min-width: 0; font-weight: 400; overflow-wrap: anywhere; }
.iv-description-field > :deep(dd) { min-width: 0; margin: 0; }
.iv-description-field :deep(.jdp) { width: 100%; }
.iv-description-field :deep(.jdp-trigger) { color: #111a3d; font-size: .82rem; font-weight: 700; }
.iv-card-actions { display: flex; gap: .35rem; flex: none; }
.iv-card-action { display: inline-flex; align-items: center; justify-content: center; width: 1.75rem; height: 1.75rem; padding: 0; border: 0; border-radius: 50%; color: #fff; font-size: .9rem; cursor: pointer; }
.iv-card-action-edit { background: #2563eb; }
.iv-card-action-edit:hover:not(:disabled) { background: #1d4ed8; }
.iv-card-action-delete { background: #ef4444; }
.iv-card-action-delete:hover:not(:disabled) { background: #dc2626; }
.iv-panel { --vap-accent: #4f46e5; --vap-accent-hover: #3730a3; min-width: 0; display: flex; flex-direction: column; gap: .6rem; padding: .75rem .8rem; border: 1px solid #d7e3f3; border-radius: 8px; background: #fff; transition: background-color .15s, border-color .15s; }
.iv-panel-active { border-color: #a5b4fc; box-shadow: 0 0 0 1px #a5b4fc; }
.iv-panel-selected { --vap-accent: #2f8f68; --vap-accent-hover: #24704f; border-color: #8fd0b2; background: #eaf7f0; box-shadow: none; }
.iv-panel-head { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem .5rem; }
.iv-panel-head h4 { flex: 1 1 auto; margin: 0; font-size: 1rem; font-weight: 700; color: #111a3d; }
.iv-chip { display: inline-flex; margin-left: auto; align-items: center; gap: .25rem; max-width: 100%; padding: .2rem .6rem; border-radius: 20px; background: #e6eefb; color: #536481; font-size: .72rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.iv-chip-muted { background: #eef1f6; color: #8693a8; }
.iv-empty-optimized p { display: flex; align-items: center; gap: .6rem; margin: 0; padding: .55rem .6rem; border-radius: 6px; background: #f3f6fb; color: #647389; font-size: .8rem; line-height: 1.4; }
.iv-optimizer { display: flex; align-items: center; justify-content: flex-end; padding-right: .6rem; gap: .3rem; margin: -.25rem 0 0; color: #647389; font-size: .75rem; overflow-wrap: anywhere; }
.iv-empty-optimized { display: grid; gap: .45rem; }
.iv-empty-optimized .iv-warning { background: #fff6e0; color: #8a5a00; }
.iv-empty-optimized .iv-warning .mdi { color: #c58a00; }
.iv-empty-optimized .mdi { flex: none; font-size: 1.3rem; color: #7b879a; }
.iv-carousel { position: relative; min-width: 0; }
.iv-track { display: flex; overflow-x: auto; scroll-snap-type: x mandatory; scrollbar-width: none; overscroll-behavior-x: contain; }
.iv-track::-webkit-scrollbar { display: none; }
.iv-slide { flex: 0 0 100%; min-width: 0; scroll-snap-align: center; scroll-snap-stop: always; }
.iv-dots { display: flex; justify-content: center; gap: .15rem; margin-top: .1rem; }
.iv-dot { position: relative; width: 22px; height: 22px; padding: 0; border: 0; background: transparent; cursor: pointer; }
.iv-dot::after { content: ''; position: absolute; inset: 7px; border-radius: 50%; background: #c3cbd8; transition: background-color .15s, transform .15s; }
.iv-dot-current::after { background: var(--vap-accent); transform: scale(1.15); }
.iv-optimize { flex: 0 0 auto; align-self: stretch; }
.iv-meta { margin: .35rem 0 0; font-size: .77rem; display: grid; gap: .4rem; }
.iv-meta > div { display: grid; grid-template-columns: minmax(4rem, .8fr) minmax(0, 1.4fr); gap: .5rem; align-items: baseline; }
.iv-meta dt { font-weight: 400; color: #596984; }
.iv-meta dd { margin: 0; overflow-wrap: anywhere; }
.iv-card-footer { display: flex; margin-top: auto; padding-top: .35rem; }
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
  .iv-button { min-height: 44px; }
}
</style>
