<template>
  <VoiceDialog :title="label('generate')" :busy="busy" @close="$emit('close')" @submit="submit">
    <label class="gid-field">{{ label('prompt') }}
      <textarea v-model="prompt" rows="4" required maxlength="2000" :disabled="busy" :placeholder="label('promptPlaceholder')"></textarea>
    </label>
    <label class="gid-field">{{ label('configuration') }}
      <select v-model="configurationName" :disabled="busy || loading || !configurations.length">
        <option value="">{{ label('automatic') }}</option>
        <option v-for="configuration in configurations" :key="configuration.config_name" :value="configuration.config_name">
          {{ configuration.config_name }}<template v-if="configuration.provider"> · {{ configuration.provider }}</template>
        </option>
      </select>
    </label>
    <p v-if="loadFailed" class="gid-notice gid-warning" role="alert">{{ label('loadError') }}</p>
    <p v-else-if="!loading && !configurations.length" class="gid-notice gid-warning" role="status">{{ label('none') }}</p>
    <p v-if="error" class="gid-notice gid-error" role="alert">{{ error }}</p>
    <template #footer>
      <button type="submit" class="gid-submit" :disabled="!canSubmit">
        <i class="mdi" :class="busy ? 'mdi-loading mdi-spin' : 'mdi-image-auto-adjust'" aria-hidden="true"></i>
        {{ busy ? label('generating') : label('generate') }}
      </button>
    </template>
  </VoiceDialog>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import VoiceDialog from './VoiceDialog.vue'
import { listServiceConfigs } from '../api/configurationClient'
import { useContentLanguage } from '../composables/useContentLanguage'
import { generateImageLabel } from '../utils/localizedLabels'
import type { ConfigurationFileSummaryResponse } from '../types/configurationSchemas'

const props = defineProps<{ busy: boolean; error: string }>()
const emit = defineEmits<{ close: []; submit: [prompt: string, configurationName: string | null] }>()
const language = useContentLanguage()
const label = (key: Parameters<typeof generateImageLabel>[0]): string => generateImageLabel(key, language.value)

const prompt = ref('')
const configurationName = ref('')
const configurations = ref<ConfigurationFileSummaryResponse[]>([])
const loading = ref(true)
const loadFailed = ref(false)
// Only enabled configurations can generate; with none, generation could only fail.
const canSubmit = computed(() => !props.busy && !loading.value && !!prompt.value.trim() && configurations.value.length > 0)

function submit(): void {
  if (canSubmit.value) emit('submit', prompt.value.trim(), configurationName.value || null)
}

onMounted(async () => {
  try {
    configurations.value = (await listServiceConfigs('text_to_image')).filter((file) => file.valid_json && file.enabled)
  } catch {
    loadFailed.value = true
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.gid-field { display: grid; gap: .35rem; margin-bottom: 1rem; font-size: .85rem; font-weight: 600; color: #334155; }
.gid-field textarea, .gid-field select { width: 100%; padding: .55rem .7rem; border: 1px solid #cbd5e1; border-radius: 6px; font: inherit; font-weight: 400; color: #111a3d; background: #fff; }
.gid-field textarea { resize: vertical; min-height: 6rem; }
.gid-field textarea:focus-visible, .gid-field select:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 1px; }
.gid-notice { margin: 0 0 .5rem; padding: .55rem .7rem; border-radius: 6px; font-size: .8rem; line-height: 1.4; }
.gid-warning { background: #fff6e0; color: #8a5a00; }
.gid-error { background: #fdecec; color: #b42318; }
.gid-submit { display: inline-flex; align-items: center; justify-content: center; gap: .4rem; min-height: 44px; padding: .5rem 1rem; border: 0; border-radius: 6px; background: #4f46e5; color: #fff; font-size: .85rem; font-weight: 600; cursor: pointer; }
.gid-submit:disabled { opacity: .5; cursor: default; }
.gid-submit:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 2px; }
</style>
