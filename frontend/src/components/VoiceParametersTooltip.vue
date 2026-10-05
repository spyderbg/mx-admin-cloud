<template>
  <span class="vpt-trigger" :class="{ 'vpt-controlled': open !== undefined, 'vpt-open': open }" tabindex="0">
    <slot>{{ provider }}</slot>
    <div class="vpt-box" role="tooltip">
      <div v-if="entries.length === 0" class="vpt-empty">No generation parameters recorded.</div>
      <div v-else class="vpt-row" v-for="[key, value] in entries" :key="key">
        <span class="vpt-key">{{ key }}</span>
        <span class="vpt-value">{{ value }}</span>
      </div>
    </div>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  provider?: string
  parameters?: Record<string, unknown>
  open?: boolean
}>(), { open: undefined })

const entries = computed<[string, string][]>(() =>
  Object.entries(props.parameters ?? {}).map(([key, value]) => [
    key,
    typeof value === 'string' ? value : JSON.stringify(value),
  ]),
)
</script>

<style scoped>
.vpt-trigger {
  position: relative;
  display: inline-block;
  cursor: default;
}
.vpt-box {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 50%;
  transform: translateX(-50%);
  min-width: 240px;
  max-width: 420px;
  border: 1.5px solid #b8d4f0;
  border-radius: 10px;
  background: #f8fbff;
  box-shadow: 0 6px 16px rgba(15, 23, 42, 0.15);
  padding: 0.5rem 0.65rem;
  text-align: left;
  z-index: 30;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.12s ease;
  pointer-events: none;
}
.vpt-box::after {
  content: '';
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  border: 6px solid transparent;
  border-top-color: #b8d4f0;
}
.vpt-box::before {
  content: '';
  position: absolute;
  top: calc(100% - 1.5px);
  left: 50%;
  transform: translateX(-50%);
  border: 5px solid transparent;
  border-top-color: #f8fbff;
  z-index: 1;
}
.vpt-trigger:not(.vpt-controlled):hover .vpt-box,
.vpt-trigger:not(.vpt-controlled):focus-visible .vpt-box,
.vpt-trigger:not(.vpt-controlled):focus-within .vpt-box,
.vpt-trigger.vpt-open .vpt-box {
  opacity: 1;
  visibility: visible;
}
.vpt-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.75rem;
  font-size: 0.72rem;
  line-height: 1.5;
}
.vpt-row + .vpt-row {
  margin-top: 0.15rem;
}
.vpt-key {
  font-weight: 700;
  color: #0f766e;
  white-space: nowrap;
}
.vpt-value {
  color: #374151;
  text-align: right;
  overflow-wrap: anywhere;
}
.vpt-empty {
  font-size: 0.72rem;
  color: #6b7280;
}
</style>
