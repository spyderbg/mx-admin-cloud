<template>
  <div class="vgd">
    <dt>{{ copy.generation }}</dt>
    <dd>
      <JsonDetailsPopup :title="copy.details" :data="parameters || {}" :open="open" :top-inset="72"
        :copy-label="copy.copyJson" :copied-label="copy.copied" :copy-error-label="copy.copyError" :empty-label="copy.noDetails"
        @update:open="$emit('update:open', $event)">
        {{ copy.viewDetails }}
      </JsonDetailsPopup>
    </dd>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'
import JsonDetailsPopup from './JsonDetailsPopup.vue'

withDefaults(defineProps<{ parameters?: Record<string, unknown>; open?: boolean }>(), { open: undefined })
defineEmits<{ 'update:open': [open: boolean] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
</script>
