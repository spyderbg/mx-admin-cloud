<template>
  <svg
    class="service-icon"
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path v-for="(path, index) in paths" :key="index" :d="path" />
  </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ service: string }>()

const servicePaths: Record<string, string[]> = {
  content: [
    'M3 7V5a2 2 0 0 1 2-2h5l3 4h6a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z',
    'M3 7h10M8 12h8M8 16h5',
  ],
  logging: [
    'M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Z',
    'M14 3v6h6M8 9h2M8 13h8M8 17h6',
  ],
  text_to_image: [
    'M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z',
    'M3 17l6-6 4 4 3-3 5 5',
    'M17 7a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Z',
  ],
  text_to_speech: [
    'M5 3h14a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H9l-6 3V5a2 2 0 0 1 2-2Z',
    'M8 9v3M12 7v7M16 8v5',
  ],
  text_translation: [
    'M3 5h12M9 3v2M5 5c1 5 4 7 8 9M13 5c-1 4-4 7-9 9',
    'M13 21l4-10 4 10M14.5 17h5',
  ],
  voice_optimization: [
    'M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4',
  ],
}

const paths = computed(() => servicePaths[props.service] ?? [
  'M4 7h16M4 17h16M8 4v6M16 14v6',
])
</script>

<style scoped>
.service-icon { display: inline-block; width: 1em; height: 1em; flex: none; vertical-align: middle; }
</style>
