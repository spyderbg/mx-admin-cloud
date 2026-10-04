<template>
  <Teleport to="body">
    <div v-if="show" class="cd-modal-overlay" @click.self="close" @keydown.esc.stop.prevent="close">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ addNewCategoryTitleLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" :disabled="creating" @click="close">&times;</button>
        </div>
        <div class="cd-modal-body">
          <div class="form-group mb-3">
            <label class="col-form-label">{{ categoryNameEnglishLabel(contentLanguage) }}</label>
            <input
              ref="nameInput"
              v-model="name"
              type="text"
              class="form-control"
              :class="{ 'is-invalid': Boolean(validationMessage) }"
              :placeholder="enterCategoryNameLabel(contentLanguage)"
              :disabled="creating"
              @keyup.enter="confirm"
            />
            <small v-if="validationMessage" class="text-danger">{{ validationMessage }}</small>
            <small v-else-if="normalized" class="text-muted">
              {{ categoryIdLabel(contentLanguage) }}: <code>{{ previewId }}</code>
            </small>
          </div>
          <div class="alert alert-info mb-0 py-2">
            <small>
              <i class="mdi mdi-information-outline mr-1"></i>
              {{ categoryGenerationNoticeLabel(contentLanguage) }}
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
import { computed, nextTick, ref, watch } from 'vue'
import type { AxiosError } from 'axios'
import { categoriesCreate } from '../api/contentClient'
import { useContentLanguage } from '../composables/useContentLanguage'
import { categoryIdFromFolderName, normalizeCategoryName } from '../utils/categoryIds'
import {
  addNewCategoryTitleLabel,
  cancelLabel,
  categoryAlreadyExistsLabel,
  categoryGenerationNoticeLabel,
  categoryIdLabel,
  categoryNameEnglishLabel,
  categoryNameRulesLabel,
  createLabel,
  creatingLabel,
  enterCategoryNameLabel,
} from '../utils/localizedLabels'

const props = defineProps<{
  show: boolean
  /** Folder names already taken, so a duplicate is caught before the round trip. */
  existingFolderNames: string[]
}>()

const emit = defineEmits<{
  close: []
  created: [folderName: string, message: string]
}>()

const contentLanguage = useContentLanguage()

const name = ref('')
const creating = ref(false)
const errorMessage = ref('')
const nameInput = ref<HTMLInputElement | null>(null)

const normalized = computed(() => normalizeCategoryName(name.value))
const previewId = computed(() => categoryIdFromFolderName(normalized.value))

const isDuplicate = computed(() => props.existingFolderNames.includes(normalized.value))
const isSyntaxValid = computed(() => /^[a-z0-9_]+$/.test(normalized.value))

const validationMessage = computed(() => {
  if (!normalized.value) return ''
  if (!isSyntaxValid.value) return categoryNameRulesLabel(contentLanguage.value)
  if (isDuplicate.value) return categoryAlreadyExistsLabel(contentLanguage.value)
  return ''
})

const canCreate = computed(
  () => Boolean(normalized.value) && isSyntaxValid.value && !isDuplicate.value && !creating.value,
)

watch(
  () => props.show,
  async (visible) => {
    if (!visible) return
    name.value = ''
    errorMessage.value = ''
    creating.value = false
    await nextTick()
    nameInput.value?.focus()
  },
)

function close(): void {
  if (creating.value) return
  emit('close')
}

async function confirm(): Promise<void> {
  if (!canCreate.value) return
  creating.value = true
  errorMessage.value = ''
  try {
    // Runs translation, TTS for every language and icon generation server-side, so this
    // request can take tens of seconds — the modal stays open and locked meanwhile.
    const result = await categoriesCreate({ name: normalized.value })
    emit('created', result.category, result.message)
  } catch (err) {
    const axiosErr = err as AxiosError<{ detail?: string }>
    errorMessage.value =
      axiosErr.response?.data?.detail || 'Could not create the category. Please try again.'
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
</style>
