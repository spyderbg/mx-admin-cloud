<template>
  <div class="id-image-card" :class="{ 'id-image-card-main': isMain }">
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
    <div class="id-image-meta">
      <div class="id-meta-label">{{ generatorLabel(contentLanguage) }}</div>
      <strong>{{ generator }}</strong>
      <div class="id-meta-label id-meta-spaced">{{ promptLabel(contentLanguage) }}</div>
      <p>{{ prompt }}</p>
      <span class="id-mobile-image-file">{{ fileName }}</span>
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
    </div>
    <div v-if="!readonly" class="file-actions" @click.stop>
      <button type="button" class="btn-icon btn-icon-edit" title="Replace" aria-label="Replace image" @click.stop="emit('replace')">
        <i class="mdi mdi-pencil"></i>
      </button>
      <button type="button" class="btn-icon btn-icon-delete" title="Delete" aria-label="Delete image" @click.stop="emit('delete')">
        <i class="mdi mdi-delete"></i>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useContentLanguage } from '../composables/useContentLanguage'
import { generatorLabel, promptLabel, selectedLabel, selectImageLabel } from '../utils/localizedLabels'

defineProps<{
  src: string
  title: string
  generator: string
  prompt: string
  fileName: string
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
</script>

<style scoped>
.id-image-card {
  position: relative;
  display: grid;
  grid-template-columns: minmax(10rem, 43%) minmax(0, 1fr);
  gap: 1.25rem;
  min-height: 12.8rem;
  border: 1px solid #dfe5ef;
  border-radius: 6px;
  padding: 0.75rem;
}
.id-image-card-main {
  border-color: #2f8f68;
  box-shadow: inset 0 0 0 1px #2f8f68;
}
.id-meta-label {
  color: #6d7391;
  font-size: 0.78rem;
  font-weight: 700;
  line-height: 1;
}
.id-mobile-image-file {
  display: none;
}
.id-image-preview {
  position: relative;
  min-width: 0;
  aspect-ratio: 1 / 1;
  overflow: hidden;
  border-radius: 6px;
  background: #f6f8fc;
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
.file-actions {
  position: absolute;
  top: 0.35rem;
  right: 0.35rem;
  display: flex;
  gap: 0.35rem;
  z-index: 2;
  cursor: default;
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
.id-image-meta {
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 0.35rem 0 0.05rem;
}
.id-image-meta strong {
  color: #111a3d;
  font-size: 0.9rem;
  margin-top: 0.5rem;
}
.id-meta-spaced {
  margin-top: 1.05rem;
}
.id-image-meta p {
  color: #111a3d;
  font-size: 0.84rem;
  line-height: 1.55;
  margin: 0.45rem 0 0;
}
.id-release-btn {
  align-self: center;
  width: 100%;
  max-width: 10.5rem;
  min-width: 0;
  min-height: 2.1rem;
  border: 1px solid #2f8f68;
  border-radius: 4px;
  background: #fff;
  color: #2f8f68;
  font-size: 0.84rem;
  font-weight: 700;
  margin-top: auto;
}
.id-release-btn i {
  font-size: 1rem;
  margin-right: 0.35rem;
}
.id-release-btn:hover {
  background: #eefaf5;
}
.id-release-btn-active,
.id-release-btn-active:hover,
.id-release-btn:disabled {
  border-color: #cbd5e1;
  background: #f1f5f9;
  color: #64748b;
  cursor: default;
}
@media (max-width: 575px) {
  .id-image-card {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    min-height: 0;
    padding: 0.5rem;
  }
  .id-image-card-main::before {
    position: absolute;
    top: 0.4rem;
    left: 0.4rem;
    z-index: 2;
    display: grid;
    place-items: center;
    width: 1.65rem;
    height: 1.65rem;
    border-radius: 50%;
    background: #2f8f68;
    color: #fff;
    content: '✓';
    font-weight: 700;
  }
  .id-image-meta {
    flex: 1;
    padding: 0;
  }
  .id-image-meta .id-meta-label,
  .id-image-meta p {
    display: none;
  }
  .id-image-meta strong {
    margin: 0 0 0.2rem;
    font-size: 0.82rem;
  }
  .id-mobile-image-file {
    display: block;
    overflow: hidden;
    color: #64748b;
    font-size: 0.75rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .id-release-btn {
    max-width: none;
    min-height: 2.35rem;
    margin-top: 0.65rem;
  }
}
</style>
