<template>
  <div class="vap">
    <audio ref="audio" :src="src || undefined" preload="metadata" @loadedmetadata="readDuration" @durationchange="readDuration"
      @timeupdate="position = audio?.currentTime || 0" @ended="playing = false" @error="onError" />
    <div class="vap-controls">
      <button type="button" class="vap-play" :disabled="!src" :aria-label="`${playing ? copy.pause : copy.play}: ${label}`" @click="toggle">
        <i :class="playing ? 'mdi mdi-pause' : 'mdi mdi-play'" aria-hidden="true"></i>
      </button>
      <input class="vap-timeline" type="range" min="0" :max="duration || 1" step="0.01" :value="position" :disabled="!duration"
        :aria-label="`${copy.seek}: ${label}`" :aria-valuetext="`${formatTime(position)} / ${formatTime(duration)}`" @input="seek" />
      <span class="vap-time">{{ duration ? formatTime(playing ? position : Math.ceil(duration)) : '—:—' }}</span>
    </div>
    <p v-if="error" class="vap-error" role="alert">{{ copy.playbackError }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'

const props = defineProps<{ src: string; label: string; active: boolean }>()
const emit = defineEmits<{ play: []; duration: [seconds: number] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
const audio = ref<HTMLAudioElement | null>(null)
const playing = ref(false)
const position = ref(0)
const duration = ref(0)
const error = ref(false)
let playAttempt = 0

function formatTime(value: number): string {
  const seconds = Math.floor(value)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}
function readDuration(): void {
  duration.value = Number.isFinite(audio.value?.duration) ? audio.value!.duration : 0
  if (duration.value) emit('duration', duration.value)
}
function stop(): void {
  playAttempt++
  audio.value?.pause()
  playing.value = false
}
function onError(): void {
  if (props.src) error.value = true
  stop()
}
async function toggle(): Promise<void> {
  if (!audio.value || !props.src) return
  if (playing.value) { stop(); return }
  error.value = false
  const attempt = ++playAttempt
  emit('play')
  playing.value = true
  try { await audio.value.play() }
  catch {
    if (attempt === playAttempt) { error.value = true; playing.value = false }
  }
}
function seek(event: Event): void {
  if (audio.value) audio.value.currentTime = Number((event.target as HTMLInputElement).value)
}
watch(() => props.active, (active) => { if (!active) stop() })
watch(() => props.src, () => { stop(); position.value = 0; duration.value = 0; error.value = false })
onBeforeUnmount(stop)
</script>

<style scoped>
.vap { min-width: 0; }
.vap-controls { display: flex; align-items: center; gap: .8rem; min-height: 3rem; }
.vap-play { display: grid; place-items: center; width: 44px; height: 44px; flex: 0 0 44px; border: 0; border-radius: 50%; background: var(--vap-accent, #4f46e5); color: white; font-size: 1.7rem; cursor: pointer; }
.vap-play:hover { background: var(--vap-accent-hover, #3730a3); }
.vap-play:disabled { background: #cbd5e1; cursor: default; }
.vap-play:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 3px; }
.vap-timeline { min-width: 0; width: 100%; height: 32px; accent-color: var(--vap-accent, #4f46e5); cursor: pointer; }
.vap-time { flex: none; font-size: .75rem; color: #475569; font-variant-numeric: tabular-nums; }
.vap-error { color: #b91c1c; font-size: .78rem; margin: .4rem 0 0; }
</style>
