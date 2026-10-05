<template>
  <div ref="root" class="vgd" @mouseenter="show" @mouseleave="onMouseLeave" @focusin="show" @focusout="onFocusOut">
    <dt>{{ copy.details }}:</dt>
    <dd>
      <button ref="trigger" type="button" class="vgd-trigger" :aria-label="copy.details" aria-haspopup="dialog" :aria-expanded="open" @click="show">
        {{ copy.viewDetails }}
      </button>
      <div v-if="open" class="vgd-popup" role="dialog" :aria-label="copy.details">
        <header class="vgd-header">
          <strong>{{ copy.details }}</strong>
          <button type="button" class="vgd-copy" :disabled="copyState === 'pending'" @click="copyJson">
            <i class="mdi" :class="copyState === 'copied' ? 'mdi-check' : 'mdi-content-copy'" aria-hidden="true"></i>
            <span aria-live="polite">{{ copyState === 'copied' ? copy.copied : copy.copyJson }}</span>
          </button>
        </header>
        <dl v-if="metadata.length" class="vgd-meta">
          <div v-for="entry in metadata" :key="entry.label"><dt>{{ entry.label }}</dt><dd>{{ entry.value }}</dd></div>
        </dl>
        <p v-else class="vgd-empty">{{ copy.noDetails }}</p>
        <p v-if="copyState === 'error'" class="vgd-error" role="alert">{{ copy.copyError }}</p>
      </div>
    </dd>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'

const props = withDefaults(defineProps<{
  parameters?: Record<string, unknown>
  metadata: { label: string; value: string }[]
  open?: boolean
}>(), { open: undefined })
const emit = defineEmits<{ 'update:open': [open: boolean] }>()
const language = useContentLanguage()
const copy = computed(() => itemVoiceCopy[language.value])
const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const localOpen = ref(false)
const open = computed({
  get: () => props.open ?? localOpen.value,
  set: (value: boolean) => {
    if (props.open === undefined) localOpen.value = value
    else emit('update:open', value)
  },
})
const copyState = ref<'idle' | 'pending' | 'copied' | 'error'>('idle')

function show(): void {
  if (!open.value) copyState.value = 'idle'
  open.value = true
}

function onMouseLeave(): void {
  if (!root.value?.contains(document.activeElement)) open.value = false
}

function onFocusOut(event: FocusEvent): void {
  if (!(event.relatedTarget instanceof Node) || !root.value?.contains(event.relatedTarget)) open.value = false
}

function onPointerDown(event: PointerEvent): void {
  if (event.target instanceof Node && !root.value?.contains(event.target)) open.value = false
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || !open.value) return
  event.preventDefault()
  event.stopPropagation()
  trigger.value?.focus()
  open.value = false
}

async function copyJson(): Promise<void> {
  copyState.value = 'pending'
  try {
    await navigator.clipboard.writeText(JSON.stringify(props.parameters || {}, null, 2))
    copyState.value = 'copied'
  } catch {
    copyState.value = 'error'
  }
}

watch(() => props.parameters, () => { copyState.value = 'idle' })
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
.vgd-trigger { display: inline-flex; align-items: center; gap: .25rem; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; cursor: pointer; }
.vgd-popup { position: absolute; bottom: calc(100% + 8px); left: 0; z-index: 30; width: min(320px, calc(100vw - 96px)); border: 1.5px solid #b8d4f0; border-radius: 10px; background: #f8fbff; box-shadow: 0 6px 16px #0f172a26; padding: .7rem; color: #334155; }
.vgd-popup::before { content: ''; position: absolute; top: 100%; left: 50%; transform: translateX(-50%); border: 6px solid transparent; border-top-color: #b8d4f0; }
.vgd-popup::after { content: ''; position: absolute; left: 0; right: 0; bottom: -9px; height: 9px; }
.vgd-header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .5rem; margin-bottom: .65rem; }
.vgd-header > strong { font-size: .8rem; }
.vgd-copy { display: inline-flex; flex: none; align-items: center; gap: .3rem; min-height: 32px; border: 1px solid #cbd8ec; border-radius: 6px; padding: .3rem .5rem; background: #fff; color: #1757b9; font: inherit; font-size: .72rem; cursor: pointer; }
.vgd-copy:hover { background: #eef4ff; }
.vgd-copy:disabled { opacity: .5; cursor: default; }
.vgd-meta { display: grid; gap: .4rem; margin: 0; font-size: .77rem; }
.vgd-meta > div { display: grid; grid-template-columns: minmax(4rem, .8fr) minmax(0, 1.4fr); gap: .5rem; align-items: baseline; }
.vgd-meta dt { font-weight: 400; color: #596984; }
.vgd-meta dd { margin: 0; overflow-wrap: anywhere; }
.vgd-empty { margin: 0; color: #64748b; }
.vgd-error { margin: .65rem 0 0; color: #b91c1c; }
button:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 2px; }
@media (max-width: 575px) {
  .vgd-trigger, .vgd-copy { min-height: 44px; }
}
</style>
