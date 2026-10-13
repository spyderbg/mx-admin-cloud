<template>
  <span class="ctb">
    <button type="button" class="ctb-button" :class="{ 'ctb-error': state === 'error' }" :aria-label="label"
      :title="state === 'copied' ? copiedLabel : state === 'error' ? errorLabel : label" :disabled="state === 'pending'" @click="copy">
      <i class="mdi" :class="icon" aria-hidden="true"></i>
    </button>
    <span class="ctb-status" role="status">{{ state === 'copied' ? copiedLabel : state === 'error' ? errorLabel : '' }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

// Copies `text` as it is; JsonDetailsPopup's button copies its data as JSON instead.
const props = defineProps<{ text: string; label: string; copiedLabel: string; errorLabel: string }>()
const state = ref<'idle' | 'pending' | 'copied' | 'error'>('idle')
const icon = computed(() => {
  if (state.value === 'copied') return 'mdi-check'
  return state.value === 'error' ? 'mdi-alert-circle-outline' : 'mdi-content-copy'
})
let resetTimer: ReturnType<typeof setTimeout> | undefined

async function copy(): Promise<void> {
  clearTimeout(resetTimer)
  state.value = 'pending'
  try {
    await navigator.clipboard.writeText(props.text)
    state.value = 'copied'
    resetTimer = setTimeout(() => { state.value = 'idle' }, 2000)
  } catch {
    state.value = 'error'
  }
}

watch(() => props.text, () => {
  clearTimeout(resetTimer)
  state.value = 'idle'
})
onBeforeUnmount(() => clearTimeout(resetTimer))
</script>

<style scoped>
.ctb { display: inline-flex; flex: none; }
.ctb-button { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 24px; padding: 0; border: 0; border-radius: 4px; background: transparent; color: #596984; font-size: .85rem; cursor: pointer; }
.ctb-button:hover { color: #1757b9; background: #eef4ff; }
.ctb-button:disabled { opacity: .5; cursor: default; }
.ctb-button:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 2px; }
.ctb-error { color: #b91c1c; }
.ctb-status { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
@media (max-width: 575px) {
  .ctb-button { width: 44px; height: 44px; }
}
</style>
