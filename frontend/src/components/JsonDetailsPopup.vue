<template>
  <div ref="root" class="jdp" @focusout="onFocusOut" @keydown.esc.prevent.stop="close">
    <button ref="trigger" type="button" class="jdp-trigger" :aria-label="title" aria-haspopup="dialog" :aria-expanded="open"
      @mouseenter="show" @mouseleave="onMouseLeave" @focus="show" @click="show">
      <slot>{{ title }}</slot>
    </button>
    <button type="button" class="jdp-copy" :aria-label="`${copyLabel}: ${title}`" :title="copyState === 'copied' ? copiedLabel : copyLabel"
      :disabled="copyState === 'pending'" @click="copyJson">
      <i class="mdi" :class="copyState === 'copied' ? 'mdi-check' : 'mdi-content-copy'" aria-hidden="true"></i>
    </button>
    <span class="jdp-status" role="status">{{ copyState === 'copied' ? copiedLabel : copyState === 'error' ? copyErrorLabel : '' }}</span>
    <div v-if="open" ref="popup" class="jdp-popup" :class="{ 'jdp-below': below }" :style="{ '--jdp-content-height': `${contentHeight}px`, '--jdp-left': `${popupLeft}px`, '--jdp-arrow-left': `${arrowLeft}px` }" role="dialog" :aria-label="title"
      @mouseenter="clearCloseTimer" @mouseleave="onMouseLeave">
      <dl v-if="entries.length" class="jdp-content" tabindex="0" :aria-label="title">
        <div v-for="[key, value] in entries" :key="key" class="jdp-row">
          <dt>{{ key }}</dt>
          <dd>{{ formatValue(value) }}</dd>
        </div>
      </dl>
      <span v-else class="jdp-empty">{{ emptyLabel }}</span>
      <span v-if="copyState === 'error'" class="jdp-error" role="alert">{{ copyErrorLabel }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  title: string
  data: Record<string, unknown>
  open?: boolean
  copyLabel?: string
  copiedLabel?: string
  copyErrorLabel?: string
  emptyLabel?: string
  topInset?: number
}>(), {
  open: undefined,
  copyLabel: 'Copy JSON',
  copiedLabel: 'Copied',
  copyErrorLabel: 'Could not copy the information. Please try again.',
  emptyLabel: 'No information recorded.',
  topInset: 16,
})
const emit = defineEmits<{ 'update:open': [open: boolean] }>()
const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const popup = ref<HTMLElement | null>(null)
const below = ref(false)
const contentHeight = ref(360)
const popupLeft = ref(0)
const arrowLeft = ref(0)
const localOpen = ref(false)
const open = computed({
  get: () => props.open ?? localOpen.value,
  set: (value: boolean) => {
    if (props.open === undefined) localOpen.value = value
    else emit('update:open', value)
  },
})
const entries = computed(() => Object.entries(props.data))
const copyState = ref<'idle' | 'pending' | 'copied' | 'error'>('idle')
let closeTimer: ReturnType<typeof setTimeout> | undefined
let copyTimer: ReturnType<typeof setTimeout> | undefined

function clearCloseTimer(): void {
  clearTimeout(closeTimer)
  closeTimer = undefined
}

function show(): void {
  clearCloseTimer()
  open.value = true
}

function close(): void {
  clearCloseTimer()
  if (root.value?.contains(document.activeElement)) trigger.value?.focus()
  open.value = false
}

function onMouseLeave(): void {
  clearCloseTimer()
  // Allow the pointer to cross the space between the value and its popup.
  closeTimer = setTimeout(() => {
    if (!root.value?.contains(document.activeElement)) open.value = false
  }, 150)
}

function onFocusOut(event: FocusEvent): void {
  if (!(event.relatedTarget instanceof Node) || !root.value?.contains(event.relatedTarget)) {
    clearCloseTimer()
    open.value = false
  }
}

function onPointerDown(event: PointerEvent): void {
  if (event.target instanceof Node && !root.value?.contains(event.target)) {
    clearCloseTimer()
    open.value = false
  }
}

function formatValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value) ?? String(value)
}

function updatePosition(): void {
  if (!open.value || !popup.value || !root.value || !trigger.value) return
  const rect = trigger.value.getBoundingClientRect()
  const center = rect.left + rect.width / 2
  const width = popup.value.offsetWidth
  const left = Math.max(16, Math.min(center - width / 2, document.documentElement.clientWidth - width - 16))
  popupLeft.value = left - root.value.getBoundingClientRect().left
  // Keep the arrow on the value even when the popup shifts away from a screen edge.
  const borderWidth = parseFloat(getComputedStyle(popup.value).borderLeftWidth) || 0
  arrowLeft.value = Math.max(12, Math.min(width - 12, center - left - borderWidth))
  const aboveSpace = rect.top - props.topInset - 8
  const belowSpace = window.innerHeight - rect.bottom - 24
  below.value = aboveSpace < 120 && belowSpace > aboveSpace
  const content = popup.value.querySelector('.jdp-content')
  const chromeHeight = popup.value.offsetHeight - (content?.clientHeight ?? 0)
  contentHeight.value = Math.max(48, Math.min(360, (below.value ? belowSpace : aboveSpace) - chromeHeight))
}

async function copyJson(): Promise<void> {
  clearTimeout(copyTimer)
  copyState.value = 'pending'
  try {
    await navigator.clipboard.writeText(JSON.stringify(props.data, null, 2))
    copyState.value = 'copied'
    copyTimer = setTimeout(() => { copyState.value = 'idle' }, 2000)
  } catch {
    copyState.value = 'error'
  }
}

watch(() => props.data, () => {
  clearTimeout(copyTimer)
  copyState.value = 'idle'
})
watch(open, async (value) => {
  if (!value) return
  await nextTick()
  updatePosition()
}, { immediate: true })
onMounted(() => {
  document.addEventListener('pointerdown', onPointerDown)
  window.addEventListener('resize', updatePosition)
  window.addEventListener('scroll', updatePosition, true)
})
onBeforeUnmount(() => {
  clearCloseTimer()
  clearTimeout(copyTimer)
  document.removeEventListener('pointerdown', onPointerDown)
  window.removeEventListener('resize', updatePosition)
  window.removeEventListener('scroll', updatePosition, true)
})
</script>

<style scoped>
.jdp { position: relative; display: inline-flex; align-items: center; gap: .15rem; min-width: 0; max-width: 100%; }
.jdp-trigger { min-width: 0; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.jdp-copy { display: inline-flex; flex: none; align-items: center; justify-content: center; width: 20px; height: 24px; padding: 0; border: 0; border-radius: 4px; background: transparent; color: #596984; font: inherit; font-size: .85rem; cursor: pointer; }
.jdp-copy:hover { color: #1757b9; background: #eef4ff; }
.jdp-copy:disabled { opacity: .5; cursor: default; }
.jdp-popup { position: absolute; bottom: calc(100% + 8px); left: var(--jdp-left, 0px); z-index: 30; width: max-content; min-width: min(220px, calc(100vw - 96px)); max-width: min(320px, calc(100vw - 96px)); border: 1.5px solid #b8d4f0; border-radius: 10px; background: #f8fbff; box-shadow: 0 6px 16px #0f172a26; padding: .5rem .65rem; text-align: left; font-weight: 400; }
.jdp-popup::before { content: ''; position: absolute; top: 100%; left: var(--jdp-arrow-left, 50%); transform: translateX(-50%); border: 6px solid transparent; border-top-color: #b8d4f0; }
.jdp-popup::after { content: ''; position: absolute; left: 0; right: 0; bottom: -9px; height: 9px; }
.jdp-below { top: calc(100% + 8px); bottom: auto; }
.jdp-below::before { top: auto; bottom: 100%; border-top-color: transparent; border-bottom-color: #b8d4f0; }
.jdp-below::after { top: -9px; bottom: auto; }
.jdp-content { margin: 0; max-height: min(var(--jdp-content-height, 360px), 55vh); overflow: auto; }
.jdp-row { display: flex; align-items: baseline; justify-content: space-between; gap: .75rem; font-size: .72rem; line-height: 1.5; }
.jdp-row + .jdp-row { margin-top: .15rem; }
.jdp-row dt { max-width: 45%; font-weight: 700; color: #0f766e; overflow-wrap: anywhere; }
.jdp-row dd { flex: 1; min-width: 0; margin: 0; color: #374151; text-align: right; overflow-wrap: anywhere; }
.jdp-empty { display: block; font-size: .72rem; color: #6b7280; }
.jdp-error { display: block; margin-top: .65rem; font-size: .77rem; color: #b91c1c; }
.jdp-status { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
button:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 2px; }
@media (max-width: 575px) {
  .jdp-trigger { min-height: 44px; }
  .jdp-copy { width: 44px; height: 44px; }
}
</style>
