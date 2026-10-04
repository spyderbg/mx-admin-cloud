<template>
  <Teleport to="body">
    <div v-if="show" class="cd-modal-overlay" @click.self="close" @keydown.esc.stop.prevent="close">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ addNewItemTitleLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" :disabled="creating" @click="close">&times;</button>
        </div>
        <div class="cd-modal-body">
          <div class="form-group mb-3">
            <label class="col-form-label">{{ itemNameEnglishLabel(contentLanguage) }}</label>
            <input
              ref="nameInput"
              v-model="word"
              type="text"
              class="form-control"
              :class="{ 'is-invalid': Boolean(validationMessage) }"
              :placeholder="enterItemNameLabel(contentLanguage)"
              :disabled="creating"
            />
            <small v-if="validationMessage" class="text-danger">{{ validationMessage }}</small>
            <small v-else-if="previewId" class="text-muted">
              {{ itemIdLabel(contentLanguage) }}: <code>{{ previewId }}</code>
            </small>
          </div>

          <div class="form-group mb-3">
            <label class="col-form-label">{{ imageFileOptionalLabel(contentLanguage) }}</label>
            <input
              ref="fileInput"
              type="file"
              class="form-control-file"
              accept="image/png,image/jpeg"
              :disabled="creating"
              @change="onFileSelected"
            />
            <div v-if="previewUrl" class="aim-preview">
              <img :src="previewUrl" :alt="file?.name ?? ''" class="aim-preview-image" />
              <button type="button" class="btn btn-link btn-sm p-0" :disabled="creating" @click="clearFile">
                <i class="mdi mdi-close mr-1"></i>{{ removeImageLabel(contentLanguage) }}
              </button>
            </div>
          </div>

          <div class="alert alert-info mb-0 py-2">
            <small>
              <i class="mdi mdi-information-outline mr-1"></i>
              {{ itemGenerationNoticeLabel(contentLanguage) }}
            </small>
          </div>
          <p v-if="errorMessage" class="text-danger mb-0 mt-3">
            <i class="mdi mdi-alert-circle mr-1"></i>{{ errorMessage }}
          </p>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" :disabled="creating" @click="close">
            {{ cancelLabel(contentLanguage) }}
          </button>
          <button type="button" class="btn btn-primary" :disabled="!canCreate" @click="confirm">
            <span v-if="creating" class="spinner-border spinner-border-sm mr-1" role="status"></span>
            {{ creating ? creatingLabel(contentLanguage) : createLabel(contentLanguage) }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { AxiosError } from 'axios'
import { itemsCreate } from '../api/contentClient'
import type { CreateItemResponse } from '../types/contentSchemas'
import { useContentLanguage } from '../composables/useContentLanguage'
import { itemIdFromEnglishWord } from '../utils/itemIds'
import { resizeImageToMaxDimension } from '../utils/imageResize'
import {
  addNewItemTitleLabel,
  cancelLabel,
  createItemFailedLabel,
  createLabel,
  creatingLabel,
  enterItemNameLabel,
  imageFileOptionalLabel,
  itemAlreadyExistsLabel,
  itemGenerationNoticeLabel,
  itemIdLabel,
  itemNameEnglishLabel,
  removeImageLabel,
} from '../utils/localizedLabels'

const props = defineProps<{
  show: boolean
  categoryId: string
  /** Ids already present in the category, so a duplicate is caught before the round trip. */
  existingItemIds: string[]
}>()

const emit = defineEmits<{
  close: []
  created: [result: CreateItemResponse, word: string]
}>()

const contentLanguage = useContentLanguage()

const word = ref('')
const file = ref<File | null>(null)
const previewUrl = ref('')
const creating = ref(false)
const errorMessage = ref('')
const nameInput = ref<HTMLInputElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const previewId = computed(() => itemIdFromEnglishWord(word.value))
const isDuplicate = computed(
  () => Boolean(previewId.value) && props.existingItemIds.includes(previewId.value),
)
const validationMessage = computed(() =>
  isDuplicate.value ? itemAlreadyExistsLabel(contentLanguage.value) : '',
)
const canCreate = computed(() => Boolean(word.value.trim()) && !isDuplicate.value && !creating.value)

function revokePreview(): void {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
}

function clearFile(): void {
  revokePreview()
  file.value = null
  if (fileInput.value) fileInput.value.value = ''
}

function onFileSelected(event: Event): void {
  const input = event.target as HTMLInputElement
  const selected = input.files?.[0] ?? null
  revokePreview()
  file.value = selected
  if (selected) previewUrl.value = URL.createObjectURL(selected)
}

watch(
  () => props.show,
  async (visible) => {
    clearFile()
    if (!visible) return
    word.value = ''
    errorMessage.value = ''
    creating.value = false
    await nextTick()
    nameInput.value?.focus()
  },
)

onBeforeUnmount(revokePreview)

function close(): void {
  if (creating.value) return
  emit('close')
}

function extractErrorMessage(err: unknown): string {
  const axiosErr = err as AxiosError<{ detail?: string | { msg?: string }[] }>
  const detail = axiosErr.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    // FastAPI validation errors (422) arrive as a list of {loc, msg, type} entries.
    const messages = detail.map((entry) => entry?.msg).filter((msg): msg is string => Boolean(msg))
    if (messages.length > 0) return messages.join(' ')
  }
  return createItemFailedLabel(contentLanguage.value)
}

async function confirm(): Promise<void> {
  if (!canCreate.value) return
  creating.value = true
  errorMessage.value = ''
  const trimmed = word.value.trim()
  try {
    const formData = new FormData()
    formData.append('item', trimmed)
    // Only append the image when one was chosen; an empty part would arrive as an empty upload.
    if (file.value) formData.append('image', await resizeImageToMaxDimension(file.value))
    // Runs translation and, when a generator is active, TTS for every language server-side,
    // so this request can take tens of seconds — the modal stays open and locked meanwhile.
    const result = await itemsCreate(props.categoryId, formData)
    emit('created', result, trimmed)
  } catch (err) {
    errorMessage.value = extractErrorMessage(err)
  } finally {
    creating.value = false
  }
}
</script>

<style scoped>
.cd-modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1050;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}
.cd-modal-box {
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 5px 30px rgba(0, 0, 0, 0.3);
  width: 100%;
  max-width: 520px;
  margin: 1rem;
  overflow: hidden;
}
.cd-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid #dee2e6;
}
.cd-modal-title {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 600;
}
.cd-modal-close {
  background: none;
  border: none;
  font-size: 1.4rem;
  cursor: pointer;
  padding: 0;
  line-height: 1;
  color: #6b7280;
  flex-shrink: 0;
}
.cd-modal-close:hover {
  color: #111;
}
.cd-modal-body {
  padding: 1.25rem;
}
.cd-modal-footer {
  padding: 0.75rem 1.25rem;
  border-top: 1px solid #dee2e6;
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
.aim-preview {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-top: 0.6rem;
}
.aim-preview-image {
  width: 6rem;
  height: 6rem;
  object-fit: contain;
  border: 1px solid #dfe5ef;
  border-radius: 6px;
  background: #f6f8fc;
}
</style>
