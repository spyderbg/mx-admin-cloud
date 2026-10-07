<template>
  <VoiceDialog :title="title || deleteFileLabel(language)" :busy="busy" @close="cancel" @submit="confirm">
    <p class="delete-file-message"><slot name="message">{{ message }}</slot></p>
    <p v-if="error" class="delete-file-error" role="alert">{{ error }}</p>
    <p v-if="help" class="delete-file-help">{{ help }}</p>
    <template #footer>
      <button type="button" class="delete-file-button" :disabled="busy" autofocus @click="cancel">{{ copy.cancel }}</button>
      <button type="submit" class="delete-file-button delete-file-confirm" :disabled="busy">
        <i class="mdi" :class="busy ? 'mdi-loading mdi-spin' : 'mdi-delete'" aria-hidden="true"></i>
        {{ busy ? copy.deleting : confirmLabel || deleteLabel(language) }}
      </button>
    </template>
  </VoiceDialog>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import VoiceDialog from './VoiceDialog.vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'
import { deleteFileLabel, deleteLabel } from '../utils/localizedLabels'

const props = withDefaults(defineProps<{
  title?: string
  message?: string
  help?: string
  error?: string
  busy?: boolean
  confirmLabel?: string
}>(), { busy: false })
const emit = defineEmits<{ cancel: []; confirm: [] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
function cancel(): void { if (!props.busy) emit('cancel') }
function confirm(): void { if (!props.busy) emit('confirm') }
</script>

<style scoped>
.delete-file-message { margin: 0; line-height: 1.5; overflow-wrap: anywhere; }
.delete-file-help { margin: 1rem 0 0; color: #596984; font-size: .83rem; line-height: 1.5; }
.delete-file-error { margin: 1rem 0 0; padding: .7rem .85rem; border: 1px solid #fecaca; border-radius: 7px; background: #fef2f2; color: #991b1b; line-height: 1.5; }
.delete-file-button { display: inline-flex; justify-content: center; align-items: center; gap: .35rem; min-height: 44px; padding: .45rem .85rem; border: 1px solid #cbd5e1; border-radius: 6px; background: white; color: #475569; font-size: .85rem; font-weight: 600; cursor: pointer; }
.delete-file-confirm { border-color: #ef4444; background: #ef4444; color: white; }
.delete-file-confirm:hover:not(:disabled) { border-color: #dc2626; background: #dc2626; }
.delete-file-button:disabled { opacity: .5; cursor: default; }
.delete-file-button:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 2px; }
</style>
