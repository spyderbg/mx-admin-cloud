<template>
  <div ref="root" class="mobile-actions">
    <button
      ref="trigger"
      type="button"
      class="mobile-actions-trigger"
      :aria-label="label"
      aria-haspopup="menu"
      :aria-expanded="open"
      @click="open = !open"
    >
      <i class="mdi mdi-dots-horizontal" aria-hidden="true"></i>
    </button>
    <div v-if="open" class="mobile-actions-menu" role="menu" :aria-label="label">
      <button
        v-for="action in actions"
        :key="action.label"
        type="button"
        class="mobile-actions-item"
        :class="{ 'mobile-actions-item-danger': action.danger }"
        role="menuitem"
        :disabled="action.disabled"
        @click="runAction(action)"
      >
        <i class="mdi" :class="action.icon" aria-hidden="true"></i>
        <span>{{ action.label }}</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

export interface MobileAction {
  label: string
  icon: string
  onClick: () => void
  danger?: boolean
  disabled?: boolean
}

defineProps<{ label: string; actions: MobileAction[] }>()

const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const open = ref(false)

function runAction(action: MobileAction): void {
  open.value = false
  action.onClick()
}

function onPointerDown(event: PointerEvent): void {
  if (event.target instanceof Node && !root.value?.contains(event.target)) open.value = false
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || !open.value) return
  open.value = false
  trigger.value?.focus()
}

onMounted(() => {
  document.addEventListener('pointerdown', onPointerDown)
  document.addEventListener('keydown', onKeyDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onPointerDown)
  document.removeEventListener('keydown', onKeyDown)
})
</script>

<style scoped>
.mobile-actions {
  position: relative;
  flex: none;
  z-index: 20;
}
.mobile-actions-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.5rem;
  height: 2.5rem;
  border: 0;
  border-radius: 50%;
  background: #eaf1ff;
  color: #1757b9;
  font-size: 1.35rem;
  cursor: pointer;
}
.mobile-actions-trigger:hover,
.mobile-actions-trigger:focus-visible {
  background: #dce9ff;
  outline: 2px solid #80aeff;
  outline-offset: 2px;
}
.mobile-actions-menu {
  position: absolute;
  top: calc(100% + 0.35rem);
  right: 0;
  min-width: 12.5rem;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  border-radius: 0.75rem;
  background: #fff;
  box-shadow: 0 14px 34px rgba(17, 26, 61, 0.16);
}
.mobile-actions-item {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  width: 100%;
  min-height: 2.8rem;
  padding: 0.65rem 0.9rem;
  border: 0;
  background: #fff;
  color: #111a3d;
  font-size: 0.9rem;
  text-align: left;
  cursor: pointer;
}
.mobile-actions-item + .mobile-actions-item {
  border-top: 1px solid #eef1f6;
}
.mobile-actions-item i {
  font-size: 1.1rem;
}
.mobile-actions-item:hover,
.mobile-actions-item:focus-visible {
  background: #f3f7ff;
  outline: none;
}
.mobile-actions-item-danger,
.mobile-actions-item-danger:hover {
  color: #dc2626;
}
.mobile-actions-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
