import { ref, onUnmounted } from 'vue'
import type { Voice } from '../types/viewModels'

/**
 * Single-voice-at-a-time audio playback, shared by CategoryDetail and ItemDetailsView.
 * `resolveSrc` lets each caller apply its own cache-busting to the voice's file URI.
 */
export function useAudioPlayer(resolveSrc: (voice: Voice) => string) {
  const playingVoiceId = ref<string | null>(null)
  let activeAudio: HTMLAudioElement | null = null

  function hasAudio(voice: Voice): boolean {
    return !!(voice.fileUri && voice.fileUri.trim())
  }

  function stopAudio(): void {
    if (activeAudio) {
      activeAudio.pause()
      activeAudio.currentTime = 0
      activeAudio = null
    }
    playingVoiceId.value = null
  }

  function togglePlay(voice: Voice): void {
    if (!hasAudio(voice)) return
    if (playingVoiceId.value === voice.id) {
      stopAudio()
      return
    }
    stopAudio()
    const audio = new Audio(resolveSrc(voice))
    activeAudio = audio
    playingVoiceId.value = voice.id
    audio.addEventListener('ended', stopAudio, { once: true })
    audio.play().catch((err) => {
      console.error('Voice playback failed:', err)
      // Autoplay rejection or a broken/missing file — the audio never actually started,
      // so don't leave the UI stuck showing a "playing" state for it.
      if (playingVoiceId.value === voice.id) stopAudio()
    })
  }

  onUnmounted(stopAudio)

  return { playingVoiceId, hasAudio, togglePlay, stopAudio }
}
