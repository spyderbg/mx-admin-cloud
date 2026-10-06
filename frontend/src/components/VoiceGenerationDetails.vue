<template>
  <div class="vgd">
    <dt>{{ copy.voiceLabel }}:</dt>
    <dd>
      <JsonDetailsPopup :title="copy.settings" :data="voiceSettings" :open="open" :top-inset="72"
        :copy-label="copy.copyJson" :copied-label="copy.copied" :copy-error-label="copy.copyError" :empty-label="copy.noVoiceSettings"
        @update:open="$emit('update:open', $event)">
        {{ copy.viewSettings }}
      </JsonDetailsPopup>
    </dd>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'
import JsonDetailsPopup from './JsonDetailsPopup.vue'

const props = withDefaults(defineProps<{ parameters?: Record<string, unknown>; open?: boolean }>(), { open: undefined })
defineEmits<{ 'update:open': [open: boolean] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
const voiceSettings = computed<Record<string, unknown>>(() => {
  const settings = props.parameters?.voice_settings
  return settings && typeof settings === 'object' && !Array.isArray(settings)
    ? settings as Record<string, unknown>
    : {}
})
</script>
