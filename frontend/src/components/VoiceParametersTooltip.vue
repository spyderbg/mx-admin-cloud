<template>
  <JsonDetailsPopup :title="`${copy.provider}: ${provider || copy.unknown}`" :data="data" :open="open" :top-inset="72"
    :copy-label="copy.copyJson" :copied-label="copy.copied" :copy-error-label="copy.copyError" :empty-label="copy.noDetails"
    @update:open="$emit('update:open', $event)">
    <slot>{{ provider }}</slot>
  </JsonDetailsPopup>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'
import JsonDetailsPopup from './JsonDetailsPopup.vue'

const props = withDefaults(defineProps<{
  provider?: string
  parameters?: Record<string, unknown>
  open?: boolean
}>(), { open: undefined })
defineEmits<{ 'update:open': [open: boolean] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
const data = computed(() => ({ ...props.parameters, provider: props.provider ?? null }))
</script>
