<template>
  <article class="id-image-card" :class="{ 'id-image-card-main': isMain }" :aria-label="cardTitle">
    <header class="id-image-header">
      <h3 class="id-image-title" :title="heading">{{ cardTitle }}</h3>
      <div v-if="!readonly" class="file-actions">
        <button type="button" class="btn-icon btn-icon-edit" title="Replace" aria-label="Replace image" @click.stop="emit('replace')">
          <i class="mdi mdi-pencil"></i>
        </button>
        <button type="button" class="btn-icon btn-icon-delete" title="Delete" aria-label="Delete image" @click.stop="emit('delete')">
          <i class="mdi mdi-delete"></i>
        </button>
      </div>
    </header>
    <div
      class="id-image-preview id-image-preview-trigger"
      role="button"
      tabindex="0"
      :aria-label="`Open ${title} preview`"
      @click="emit('preview')"
      @keydown.enter.prevent="emit('preview')"
      @keydown.space.prevent="emit('preview')"
    >
      <img :src="src" :alt="title" class="id-image" />
    </div>
    <dl class="id-image-details">
      <div class="id-image-detail">
        <dt class="id-meta-label">{{ label('provider') }}</dt>
        <dd class="id-image-provider">
          <VoiceParametersTooltip :provider="provider" :parameters="configuration"
            :open="informationPopup === 'provider'" @update:open="updateInformationPopup('provider', $event)">{{ provider }}</VoiceParametersTooltip>
        </dd>
      </div>
      <div class="id-image-detail">
        <dt class="id-meta-label">{{ promptLabel(contentLanguage) }}</dt>
        <dd v-if="prompt" class="id-image-prompt">
          <JsonDetailsPopup :title="promptLabel(contentLanguage)" :data="{ prompt }" :open="informationPopup === 'prompt'" :top-inset="72"
            :copy-label="voiceCopy.copyJson" :copied-label="voiceCopy.copied" :copy-error-label="voiceCopy.copyError" :empty-label="voiceCopy.noDetails"
            @update:open="updateInformationPopup('prompt', $event)">{{ prompt }}</JsonDetailsPopup>
        </dd>
        <dd v-else class="id-image-muted" :title="provider === 'custom' ? label('uploaded') : label('noPrompt')">{{ provider === 'custom' ? label('uploaded') : label('noPrompt') }}</dd>
      </div>
    </dl>
    <button
      v-if="!readonly || isMain"
      type="button"
      class="id-release-btn"
      :class="{ 'id-release-btn-active': isMain }"
      :disabled="isMain || selectDisabled || readonly"
      @click="emit('select')"
    >
      <i class="mdi" :class="isMain ? 'mdi-check' : 'mdi-image'"></i>
      {{ isMain ? selectedLabel(contentLanguage) : selectImageLabel(contentLanguage) }}
    </button>
  </article>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import type { ImageMetadata } from '../types/viewModels'
import { itemVoiceCopy } from '../utils/itemVoiceCopy'
import { imageCardLabel, promptLabel, selectedLabel, selectImageLabel } from '../utils/localizedLabels'
import JsonDetailsPopup from './JsonDetailsPopup.vue'
import VoiceParametersTooltip from './VoiceParametersTooltip.vue'

const props = defineProps<{
  src: string
  title: string
  /** Shown above the details, e.g. "Image Variant 2". */
  heading: string
  fileName: string
  /** How the image was made, from its metadata sidecar; absent when nothing is recorded. */
  metadata?: ImageMetadata
  /** The main image is a copy of this variant, so it is marked and cannot be selected again. */
  isMain: boolean
  /** Disables selecting while another image is being set as main. */
  selectDisabled?: boolean
  /** Only preview: no select, replace or delete; the main image keeps its "Selected" mark. */
  readonly?: boolean
}>()

const emit = defineEmits<{
  preview: []
  select: []
  replace: []
  delete: []
}>()

const contentLanguage = useContentLanguage()
const label = (key: Parameters<typeof imageCardLabel>[0]): string => imageCardLabel(key, contentLanguage.value)

// Named like a voice card: the file name without its extension.
const cardTitle = computed(() => props.fileName.replace(/\.[^.]+$/, '') || props.title)

// Shown like a voice's provider: the configuration name, "custom" for an upload.
const provider = computed(() => props.metadata?.provider || 'unknown')
const prompt = computed(() => {
  const value = props.metadata?.parameters._prompt
  return typeof value === 'string' ? value.trim() : ''
})
// The configuration to copy as JSON; the prompt has a copy button of its own.
const configuration = computed(() =>
  Object.fromEntries(Object.entries(props.metadata?.parameters ?? {}).filter(([key]) => key !== '_prompt')))

// The prompt and provider details open in popups, one at a time, like a voice card's.
const voiceCopy = computed(() => itemVoiceCopy[contentLanguage.value])
type InformationPopup = 'prompt' | 'provider'
const informationPopup = ref<InformationPopup | null>(null)
function updateInformationPopup(popup: InformationPopup, open: boolean): void {
  if (open) informationPopup.value = popup
  else if (informationPopup.value === popup) informationPopup.value = null
}
</script>

<style scoped>
.id-image-card {
  container-type: inline-size;
  position: relative;
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.85rem;
  border: 1px solid #b8d4f0;
  border-radius: 10px;
  background: #f8fbff;
  padding: 1rem;
}
.id-image-card-main {
  border: 2px solid #2f8f68;
  padding: calc(1rem - 1px);
}
.id-image-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.id-image-title {
  min-width: 0;
  margin: 0;
  color: #111a3d;
  font-size: 1.05rem;
  font-weight: 700;
  line-height: 1.3;
  overflow-wrap: anywhere;
}
.file-actions {
  display: flex;
  flex: none;
  gap: 0.35rem;
}
.btn-icon {
  width: 1.75rem;
  height: 1.75rem;
  border: none;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 0.9rem;
  color: #fff;
  padding: 0;
}
.btn-icon-edit {
  background: #2563eb;
}
.btn-icon-edit:hover {
  background: #1d4ed8;
}
.btn-icon-delete {
  background: #ef4444;
}
.btn-icon-delete:hover {
  background: #dc2626;
}
.id-image-preview {
  position: relative;
  min-width: 0;
  aspect-ratio: 4 / 3;
  overflow: hidden;
  border-radius: 6px;
  background: #eef3fa;
}
.id-image-preview-trigger {
  cursor: zoom-in;
}
.id-image-preview-trigger:focus-visible {
  outline: 3px solid rgba(49, 87, 255, 0.45);
  outline-offset: 3px;
}
.id-image {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.id-image-details {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 1px solid #d7e3f3;
  border-radius: 6px;
  background: #fff;
}
.id-image-detail {
  min-width: 0;
  padding: 0.55rem 0.45rem;
}
.id-image-detail + .id-image-detail {
  border-left: 1px solid #e5edf8;
}
.id-meta-label {
  margin-bottom: 0.3rem;
  color: #596984;
  font-size: 0.75rem;
  font-weight: 400;
  line-height: 1.4;
}
.id-image-details dd {
  min-width: 0;
  margin: 0;
}
.id-image-provider :deep(.jdp-trigger) {
  color: #111a3d;
  font-size: 0.82rem;
  font-weight: 700;
}
.id-image-prompt :deep(.jdp) {
  width: 100%;
}
.id-image-prompt :deep(.jdp-trigger) {
  color: #111a3d;
  font-size: 0.82rem;
  font-weight: 700;
}
.id-image-muted {
  overflow: hidden;
  color: #64748b;
  font-size: 0.82rem;
  font-style: italic;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.id-release-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  width: 100%;
  min-width: 0;
  min-height: 36px;
  margin-top: auto;
  padding: 0.45rem 0.65rem;
  border: 1px solid #818cf8;
  border-radius: 6px;
  background: #4f46e5;
  color: #fff;
  font-size: 0.77rem;
  font-weight: 600;
  cursor: pointer;
}
.id-release-btn i {
  font-size: 1rem;
}
.id-release-btn:hover:not(:disabled) {
  background: #4338ca;
}
.id-release-btn:focus-visible {
  outline: 3px solid #a5b4fc;
  outline-offset: 2px;
}
.id-release-btn-active,
.id-release-btn-active:disabled {
  border-color: #e1f2eb;
  background: #e1f2eb;
  color: #24704f;
  opacity: 1;
  cursor: default;
}
.id-release-btn:disabled:not(.id-release-btn-active) {
  opacity: 0.5;
  cursor: default;
}
/* Two boxes need about 110px each; below that the details stack. */
@container (max-width: 230px) {
  .id-image-details {
    grid-template-columns: minmax(0, 1fr);
  }
  .id-image-detail + .id-image-detail {
    border-left: 0;
    border-top: 1px solid #e5edf8;
  }
}
@media (max-width: 575px) {
  .id-image-card {
    gap: 0.7rem;
    padding: 0.8rem;
  }
  .id-image-card-main {
    padding: calc(0.8rem - 1px);
  }
  .btn-icon {
    width: 2.75rem;
    height: 2.75rem;
  }
  .id-release-btn {
    min-height: 44px;
  }
}
</style>
