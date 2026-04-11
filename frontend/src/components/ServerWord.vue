<template>
  <div
    class="d-flex align-items-center server-word-root"
    v-bind="rootAttrs"
    :class="rootClass"
    :style="rootStyle"
  >
    <component
      :is="textTag"
      class="server-word-text"
      :class="[textTag === 'h5' ? 'mb-0' : '', textClass]"
    >{{ displayText }}</component>
    <button
      type="button"
      class="btn btn-link btn-sm text-primary p-0 server-word-edit"
      :class="buttonClass"
      :title="editTitle"
      @click="emit('edit')"
    >
      <i class="mdi mdi-pencil server-word-icon" :class="iconClass" :style="iconStyle" />
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, useAttrs, type StyleValue } from 'vue'
import type { Category } from '../types'
import { useContentLanguage } from '../composables/useContentLanguage'
import { localizedCategoryLabel } from '../utils/localizedLabels'
import { toPascalCaseWords } from '../utils/pascalCaseWords'

defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    /** When set, label follows navbar content language (`localizedCategoryLabel`). */
    category?: Category
    /** Plain label (e.g. `voice.name`); wins over `category` when non-empty after trim. */
    text?: string
    editTitle?: string
    /** When true, each whitespace-separated word is title-cased via `toPascalCaseWords`. */
    pascalCase?: boolean
    /** HTML tag for the label (default `h5`). */
    textTag?: string
    /** Extra classes on the outer flex row (space-separated). */
    rootClass?: string
    /** Extra classes on the label element. */
    textClass?: string
    /** Extra classes on the pencil button. */
    buttonClass?: string
    /** Extra classes on the MDI icon element. */
    iconClass?: string
    /** Inline style for the icon (default size comes from scoped `.server-word-icon`). */
    iconStyle?: string | Record<string, string>
  }>(),
  {
    editTitle: 'Rename category',
    pascalCase: false,
    textTag: 'h5',
  },
)

const emit = defineEmits<{
  edit: []
}>()

const attrs = useAttrs()

/** Non-class/style attrs fall through to the root (e.g. `data-testid`, `aria-label`). */
const rootAttrs = computed(() => {
  const { class: _c, style: _s, ...rest } = attrs as Record<string, unknown>
  return rest as Record<string, unknown>
})

const rootClass = computed(() => {
  const fromProps = props.rootClass?.trim()
  const fromAttrs = attrs.class
  return [fromAttrs, fromProps].filter(Boolean)
})

const rootStyle = computed((): StyleValue | undefined => {
  const s = attrs.style
  if (s == null || s === '') return undefined
  return s as StyleValue
})

const contentLanguage = useContentLanguage()

const rawLabel = computed(() => {
  const fromText = props.text?.trim()
  if (fromText) return fromText
  if (props.category)
    return localizedCategoryLabel(props.category, contentLanguage.value)
  return ''
})

const displayText = computed(() => {
  const raw = rawLabel.value
  return props.pascalCase ? toPascalCaseWords(raw) : raw
})

</script>

<style scoped>
.server-word-root {
  column-gap: 0.5rem;
}
.server-word-icon {
  font-size: 1rem;
}
.server-word-text {
  min-width: 0;
}
</style>
