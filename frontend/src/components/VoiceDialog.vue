<template>
  <Teleport to="body">
    <dialog ref="dialog" class="voice-dialog" aria-labelledby="voice-dialog-title" @cancel.prevent="close" @click="onBackdrop">
      <form class="voice-dialog-form" @submit.prevent="$emit('submit')">
        <header class="voice-dialog-header">
          <h2 id="voice-dialog-title">{{ title }}</h2>
          <button type="button" :disabled="busy" :aria-label="copy.close" @click="close"><i class="mdi mdi-close" aria-hidden="true"></i></button>
        </header>
        <div class="voice-dialog-body"><slot /></div>
        <footer class="voice-dialog-footer"><slot name="footer" /></footer>
      </form>
    </dialog>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'

const props = defineProps<{ title: string; busy: boolean }>()
const emit = defineEmits<{ close: []; submit: [] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
const dialog = ref<HTMLDialogElement | null>(null)
let previousOverflow = ''
let trigger: HTMLElement | null = null
function close(): void { if (!props.busy) emit('close') }
function onBackdrop(event: MouseEvent): void {
  if (event.target !== dialog.value || !dialog.value) return
  const rect = dialog.value.getBoundingClientRect()
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close()
}
onMounted(() => {
  trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  dialog.value?.showModal()
})
onBeforeUnmount(() => {
  dialog.value?.close()
  document.body.style.overflow = previousOverflow
  if (trigger?.isConnected) trigger.focus()
})
</script>

<style scoped>
.voice-dialog { color: #111a3d; background: white; border: 1px solid #e2e8f0; border-radius: 14px; width: min(540px, calc(100vw - 32px)); max-width: none; padding: 0; max-height: calc(100dvh - 40px); box-shadow: 0 24px 70px #0f172a30; }
.voice-dialog::backdrop { background: #0f172a80; }
.voice-dialog-form { display: flex; flex-direction: column; max-height: calc(100dvh - 42px); }
.voice-dialog-header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 1rem 1.5rem; border-bottom: 1px solid #e8edf5; flex: none; }
.voice-dialog-header h2 { font-size: 1.2rem; font-weight: 700; margin: 0; }
.voice-dialog-header button { flex: none; display: grid; place-items: center; min-width: 44px; min-height: 44px; background: none; border: 0; color: #475569; font-size: 1.4rem; cursor: pointer; border-radius: 6px; }
.voice-dialog-body { padding: 1.4rem 1.5rem; overflow-y: auto; overscroll-behavior: contain; min-height: 0; }
.voice-dialog-footer { padding: 1rem 1.5rem; border-top: 1px solid #e8edf5; display: flex; justify-content: flex-end; gap: .65rem; flex: none; }
@media (max-width: 575px) {
  .voice-dialog { width: 100%; height: 100dvh; max-height: 100dvh; max-width: 100%; margin: 0; border: 0; border-radius: 0; }
  .voice-dialog-form { height: 100%; max-height: 100%; }
  .voice-dialog-header { padding: .7rem 1rem; }
  .voice-dialog-body { flex: 1; padding: 1rem; }
  .voice-dialog-footer { padding: 1rem 1rem max(1rem, env(safe-area-inset-bottom)); }
}
</style>
