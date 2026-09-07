<template>
  <section class="category-detail-panel id-panel id-summary-panel">
    <div class="id-summary-copy">
      <div>
        <div class="id-field-label">{{ categoryIdLabel(contentLanguage) }}</div>
        <div class="id-value-row">
          <h3 class="id-item-id">{{ category.id }}</h3>
          <button type="button" class="id-edit-btn" :title="renameCategoryLabel(contentLanguage)" :aria-label="renameCategoryLabel(contentLanguage)" @click="openRenameModal">
            <i class="mdi mdi-pencil"></i>
          </button>
        </div>
      </div>

      <div>
        <div class="id-field-label">{{ categoryIconLabel(contentLanguage) }}</div>
        <div
          class="id-summary-image-preview category-icon-preview-trigger"
          role="button"
          tabindex="0"
          aria-label="Open category icon preview"
          @click="openImagePreview"
          @keydown.enter.prevent="openImagePreview"
          @keydown.space.prevent="openImagePreview"
        >
          <img
            :src="thumbnailSrc"
            alt="Category icon"
            class="id-summary-image"
          />
          <div class="category-icon-actions" @click.stop>
            <button
              type="button"
              class="btn-icon btn-icon-regenerate"
              title="Regenerate category image"
              @click.stop="requestCategoryImageRegeneration"
            >
              <i class="mdi mdi-auto-fix"></i>
            </button>
            <button
              type="button"
              class="btn-icon btn-icon-edit"
              title="Upload category icon"
              @click.stop="openUploadModal"
            >
              <i class="mdi mdi-pencil"></i>
            </button>
            <button
              type="button"
              class="btn-icon btn-icon-delete"
              :title="deleteCategoryImageLabel(contentLanguage)"
              :disabled="!category.thumbnail_uri"
              @click.stop="openDeleteModal"
            >
              <i class="mdi mdi-delete"></i>
            </button>
          </div>
        </div>
      </div>

      <div>
        <div class="id-field-label">{{ descriptionLabel(contentLanguage) }}</div>
        <p class="id-description">{{ category.description.get(contentLanguage) }}</p>
      </div>
    </div>

    <div class="id-summary-details">
      <div class="id-summary-actions id-summary-actions-only">
        <div class="id-release-group">
          <button
            type="button"
            class="btn btn-outline-danger btn-sm category-archive-btn"
            :disabled="archiving"
            @click="openArchiveModal"
          >
            <span v-if="archiving" class="spinner-border spinner-border-sm mr-1" role="status"></span>
            <i v-else class="mdi mdi-archive-arrow-down mr-1"></i>
            {{ archiveCategoryLabel(contentLanguage) }}
          </button>
        </div>
      </div>

      <div class="id-summary-voices-panel">
        <h4 class="id-section-kicker">{{ categoryVoicesLabel(contentLanguage) }}</h4>
        <VoiceCardsGrid
          :voices="category.voices"
          :playing-voice-id="playingVoiceId"
          :has-audio="hasAudio"
          :toggle-play="togglePlay"
          :upload-voice="uploadVoice"
          :delete-voice="deleteVoiceHandler"
          :rename-voice="renameVoiceHandler"
          :extra-action="regenerateAction"
        />
      </div>
    </div>
  </section>

  <!-- Rename category modal -->
  <Teleport to="body">
    <div v-if="showRename" class="cd-modal-overlay" @click.self="showRename = false">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ renameCategoryLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="showRename = false">&times;</button>
        </div>
        <div class="cd-modal-body">
          <div class="form-group mb-0">
            <label class="col-form-label">{{ categoryIdLabel(contentLanguage) }}</label>
            <input
              v-model="renameValue"
              type="text"
              class="form-control"
              :placeholder="enterCategoryIdLabel(contentLanguage)"
              @keyup.enter="confirmRename"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="showRename = false">{{ cancelLabel(contentLanguage) }}</button>
          <button type="button" class="btn btn-primary" @click="confirmRename">{{ saveLabel(contentLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Archive category confirmation modal -->
  <Teleport to="body">
    <div v-if="showArchive" class="cd-modal-overlay" @click.self="closeArchiveModal">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ archiveCategoryLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeArchiveModal">&times;</button>
        </div>
        <div class="cd-modal-body">
          <p class="mb-3">{{ archiveCategoryConfirmLabel(contentLanguage, categoryLabel) }}</p>
          <div v-if="usesComplexArchiveConfirmation" class="alert alert-warning py-2">
            <small>
              <i class="mdi mdi-alert mr-1"></i>
              {{ archiveCategoryWarningLabel(contentLanguage, category.items_count ?? category.items.length) }}
            </small>
          </div>
          <div v-if="usesComplexArchiveConfirmation" class="form-group mb-0">
            <label class="col-form-label">
              {{ archiveTypeToConfirmLabel(contentLanguage, category.id) }}
            </label>
            <input
              v-model="archiveConfirmText"
              type="text"
              class="form-control"
              :placeholder="category.id"
              @keyup.enter="confirmArchive"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeArchiveModal">
            {{ usesComplexArchiveConfirmation ? cancelLabel(contentLanguage) : noLabel(contentLanguage) }}
          </button>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="!canArchive"
            @click="confirmArchive"
          >
            {{ usesComplexArchiveConfirmation ? archiveLabel(contentLanguage) : yesLabel(contentLanguage) }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Delete category image confirmation modal -->
  <Teleport to="body">
    <div v-if="showDelete" class="cd-modal-overlay" @click.self="showDelete = false">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ deleteCategoryImageLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="showDelete = false">&times;</button>
        </div>
        <div class="cd-modal-body">
          {{ deleteCategoryImageConfirmLabel(contentLanguage, categoryLabel) }}
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="showDelete = false">{{ cancelLabel(contentLanguage) }}</button>
          <button type="button" class="btn btn-danger" @click="confirmImageDelete">{{ deleteLabel(contentLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Upload icon modal -->
  <Teleport to="body">
    <div v-if="showUpload" class="cd-modal-overlay" @click.self="closeUploadModal">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ uploadCategoryIconLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeUploadModal">&times;</button>
        </div>
        <div class="cd-modal-body">
          <div class="form-group mb-0">
            <label class="col-form-label">{{ imageFileLabel(contentLanguage) }}</label>
            <input
              type="file"
              class="form-control-file"
              accept="image/*"
              @change="onFileSelected"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeUploadModal">{{ cancelLabel(contentLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Full-screen category icon preview -->
  <Teleport to="body">
    <div v-if="showImagePreview" class="image-preview-overlay" @click.self="closeImagePreview">
      <button
        type="button"
        class="image-preview-close"
        aria-label="Close image preview"
        @click="closeImagePreview"
      >
        &times;
      </button>
      <img
        :src="thumbnailSrc"
        :alt="categoryLabel"
        class="image-preview-image"
      />
    </div>
  </Teleport>

  <!-- Result toast -->
  <Teleport to="body">
    <div
      v-if="toast"
      class="cd-toast"
      :class="toast.variant === 'error' ? 'cd-toast-error' : 'cd-toast-success'"
    >
      <i :class="toast.variant === 'error' ? 'mdi mdi-alert-circle' : 'mdi mdi-check-circle'"></i>
      <span>{{ toast.message }}</span>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, onBeforeUnmount, onMounted } from 'vue'
import type { AxiosError } from 'axios'
import VoiceCardsGrid, { type VoiceExtraAction } from './VoiceCardsGrid.vue'
import type { CategoryFull, Voice } from '../types/viewModels'
import {
  archiveCategoryConfirmLabel,
  archiveCategoryLabel,
  archiveCategoryWarningLabel,
  archiveLabel,
  archiveTypeToConfirmLabel,
  cancelLabel,
  categoryIdLabel,
  categoryIconLabel,
  categoryVoicesLabel,
  deleteCategoryImageConfirmLabel,
  deleteCategoryImageLabel,
  deleteLabel,
  descriptionLabel,
  enterCategoryIdLabel,
  imageFileLabel,
  localizedCategoryLabel,
  noLabel,
  renameCategoryLabel,
  saveLabel,
  uploadCategoryIconLabel,
  yesLabel,
} from '../utils/localizedLabels'
import { useContentLanguage } from '../composables/useContentLanguage'
import {
  categoriesIconDelete,
  categoriesIconUpdate,
  categoriesVoicesAdd,
  categoriesVoicesDelete,
  categoriesVoicesUpdate,
} from '../api/contentClient'
import {
  bumpCategoryIconVersion,
  categoryIconVersions,
  ensureCategoryDetail,
  invalidateCategoryDetail,
} from '../api/cache'
import { resizeImageToMaxDimension } from '../utils/imageResize'
import { cacheBustedUri } from '../utils/cacheBustedUri'
import { useAudioPlayer } from '../composables/useAudioPlayer'

const props = defineProps<{
  category: CategoryFull
  /** Owned by the parent, which performs the archive request and the redirect. */
  archiving?: boolean
}>()

const contentLanguage = useContentLanguage()
const categoryLabel = computed(() => localizedCategoryLabel(props.category, contentLanguage.value))

const emit = defineEmits<{
  edit: []
  delete: []
  rename: [name: string]
  /** The parent owns the route being archived, so it performs the call and redirects. */
  archive: []
  'rename-voice': [voice: Voice, name: string]
}>()

const showDelete = ref(false)
const showUpload = ref(false)
const showRename = ref(false)
const showArchive = ref(false)
const archiveConfirmText = ref('')
const usesComplexArchiveConfirmation = import.meta.env.VITE_ARCHIVE_CONFIRMATION_MODE !== 'simple'
const renameValue = ref('')
const showImagePreview = ref(false)

type ToastVariant = 'success' | 'error'
const toast = ref<{ message: string; variant: ToastVariant } | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | null = null

// Cache-busting: file URIs are stable across re-uploads (same filename), so the
// browser keeps serving the old cached file unless we force a fresh request. The icon
// version lives in the shared cache (keyed by category id) so it also busts the cache
// for the dashboard's category cards after navigating away and back.
const voiceVersions = ref<Record<string, number>>({})

const thumbnailSrc = computed(() =>
  cacheBustedUri(props.category.thumbnail_uri, categoryIconVersions.value.get(props.category.id) ?? 0),
)

function voiceSrc(voice: Voice): string {
  return cacheBustedUri(voice.fileUri, voiceVersions.value[voice.id] ?? 0)
}

const { playingVoiceId, hasAudio, togglePlay, stopAudio } = useAudioPlayer(voiceSrc)

function showToast(message: string, variant: ToastVariant): void {
  if (variant === 'error') {
    console.error(message)
  } else {
    console.log(message)
  }
  if (toastTimer) clearTimeout(toastTimer)
  toast.value = { message, variant }
  toastTimer = setTimeout(() => { toast.value = null }, 4000)
}

function extractErrorMessage(err: unknown): string {
  const axiosErr = err as AxiosError<{ detail?: string }>
  return axiosErr.response?.data?.detail || 'The operation failed. Please try again.'
}

const actionPastTense: Record<string, string> = { add: 'added', update: 'updated', delete: 'deleted' }
function pastTense(action?: string | null): string {
  return action ? (actionPastTense[action] ?? action) : 'saved'
}

async function refreshCategory(): Promise<void> {
  const name = props.category.name.en
  invalidateCategoryDetail(name)
  await ensureCategoryDetail(props.category.id, name)
}

function closeAllModals(): void {
  showDelete.value = false
  showUpload.value = false
  showRename.value = false
  showArchive.value = false
  showImagePreview.value = false
}

/**
 * Archiving is only recoverable when ARCHIVE_DIR is configured on the server, which the
 * frontend cannot detect. Complex confirmation therefore requires the id to be typed
 * out, while installations explicitly configured for the simple dialog can proceed
 * from its Yes button.
 */
const canArchive = computed(() =>
  !usesComplexArchiveConfirmation || archiveConfirmText.value.trim() === props.category.id,
)

function openArchiveModal(): void {
  closeAllModals()
  archiveConfirmText.value = ''
  showArchive.value = true
}

function closeArchiveModal(): void {
  showArchive.value = false
  archiveConfirmText.value = ''
}

function confirmArchive(): void {
  if (!canArchive.value) return
  closeArchiveModal()
  emit('archive')
}

async function deleteVoiceHandler(voice: Voice): Promise<void> {
  if (playingVoiceId.value === voice.id) stopAudio()
  try {
    const result = await categoriesVoicesDelete(props.category.id, voice.id)
    showToast(`Voice for ${voice.name} (${voice.language.toUpperCase()}) ${pastTense(result.action)}.`, 'success')
    await refreshCategory()
    voiceVersions.value = { ...voiceVersions.value, [voice.id]: (voiceVersions.value[voice.id] ?? 0) + 1 }
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}

function renameVoiceHandler(voice: Voice, name: string): void {
  emit('rename-voice', voice, name)
}

function openUploadModal(): void {
  closeAllModals()
  showUpload.value = true
}

function closeUploadModal(): void {
  showUpload.value = false
}

function openDeleteModal(): void {
  closeAllModals()
  showDelete.value = true
}

function openImagePreview(): void {
  closeAllModals()
  showImagePreview.value = true
}

function closeImagePreview(): void {
  showImagePreview.value = false
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && showUpload.value) {
    closeUploadModal()
    return
  }
  if (event.key === 'Escape' && showImagePreview.value) {
    closeImagePreview()
  }
}

onMounted(() => {
  document.addEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onDocumentKeydown)
})

function requestCategoryImageRegeneration(): void {
  showToast('Backend task pending: regenerate the category image from the saved category metadata.', 'error')
}

const regenerateAction: VoiceExtraAction = {
  icon: 'mdi-auto-fix',
  title: 'Regenerate voice',
  onClick: (voice) => showToast(`Backend task pending: regenerate ${voice.language.toUpperCase()} audio for ${voice.name}.`, 'error'),
}

async function confirmImageDelete(): Promise<void> {
  showDelete.value = false
  const uri = props.category.thumbnail_uri
  if (!uri || !uri.trim()) return

  try {
    const result = await categoriesIconDelete(props.category.id)
    showToast(`Category icon ${pastTense(result.action)}.`, 'success')
    await refreshCategory()
    bumpCategoryIconVersion(props.category.id)
    emit('delete')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}

async function uploadVoice(voice: Voice, file: File, wasAdd: boolean): Promise<void> {
  try {
    const formData = new FormData()
    if (wasAdd) formData.append('language', voice.language)
    formData.append('audio', file)
    const result = wasAdd
      ? await categoriesVoicesAdd(props.category.id, formData)
      : await categoriesVoicesUpdate(props.category.id, voice.id, formData)
    showToast(`Voice for ${voice.name} (${voice.language.toUpperCase()}) ${pastTense(result.action)}.`, 'success')
    await refreshCategory()
    voiceVersions.value = { ...voiceVersions.value, [voice.id]: (voiceVersions.value[voice.id] ?? 0) + 1 }
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}

function openRenameModal(): void {
  closeAllModals()
  renameValue.value = props.category.id
  showRename.value = true
}

function confirmRename(): void {
  const trimmed = renameValue.value.trim()
  if (trimmed && trimmed !== props.category.id) {
    emit('rename', trimmed)
  }
  showRename.value = false
}

async function onFileSelected(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  closeUploadModal()
  if (!file) return

  const formData = new FormData()
  try {
    const resizedFile = await resizeImageToMaxDimension(file)
    formData.append('image', resizedFile)
    const result = await categoriesIconUpdate(props.category.id, formData)
    showToast(`Category icon ${pastTense(result.action)}.`, 'success')
    await refreshCategory()
    bumpCategoryIconVersion(props.category.id)
    emit('edit')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  } finally {
    input.value = ''
  }
}

</script>

<style scoped>
.id-panel {
  width: 100%;
  background: #fff;
  border: 1px solid #e6eaf4;
  border-radius: 8px;
  box-shadow: 0 4px 18px rgba(25, 38, 77, 0.04);
  margin-bottom: 1rem;
  padding: 1.25rem 1.5rem;
}
.id-summary-panel {
  position: relative;
  display: grid;
  grid-template-columns: minmax(16rem, 0.7fr) minmax(0, 1.3fr);
  gap: 1.5rem;
}
.id-summary-copy {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  min-height: 100%;
}
.id-summary-details {
  padding-left: 1.5rem;
  padding-top: 4.15rem;
  display: grid;
  align-content: start;
  gap: 1.25rem;
  min-width: 0;
}
.id-field-label,
.id-section-kicker {
  color: #6d7391;
  font-size: 0.78rem;
  font-weight: 700;
  line-height: 1;
}
.id-value-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-top: 0.7rem;
}
.id-item-id {
  color: #111a3d;
  font-size: 1.6rem;
  font-weight: 700;
  line-height: 1.2;
  margin: 0;
}
.id-edit-btn {
  border: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
  padding: 0;
  background: transparent;
  color: #2563eb;
  font-size: 1.25rem;
}
.id-description {
  color: #111a3d;
  font-size: 0.98rem;
  line-height: 1.5;
  margin: 0.6rem 0 0;
}
.id-summary-image-preview {
  position: relative;
  width: 100%;
  aspect-ratio: 1 / 1;
  margin-top: 0.7rem;
  overflow: hidden;
  border: 1px solid #dfe5ef;
  border-radius: 6px;
  background: #f6f8fc;
}
.id-summary-image {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  object-position: center;
}
.id-summary-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}
.id-summary-actions-only {
  position: absolute;
  top: 1.25rem;
  right: 1.5rem;
  justify-content: flex-end;
  width: auto;
  max-width: 100%;
  min-height: 0;
}
.id-release-group {
  display: flex;
  gap: 0.55rem;
}
.category-archive-btn {
  width: 9.35rem;
  max-width: 100%;
}
.id-summary-voices-panel {
  align-self: start;
  min-width: 0;
}
.category-icon-preview-trigger {
  cursor: zoom-in;
}
.category-icon-preview-trigger:focus-visible {
  outline: 3px solid rgba(49, 87, 255, 0.45);
  outline-offset: 3px;
}
.category-icon-actions {
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
.btn-icon-regenerate {
  background: #0f766e;
}
.btn-icon-regenerate:hover {
  background: #0f5f59;
}
.btn-icon-delete {
  background: #dc2626;
}
.btn-icon-delete:hover {
  background: #b91c1c;
}
.btn-icon:disabled {
  background: #d1d5db;
  cursor: not-allowed;
  opacity: 0.7;
}
.btn-icon:disabled:hover {
  background: #d1d5db;
}
@media (max-width: 991px) {
  .id-summary-panel {
    grid-template-columns: 1fr;
  }
  .id-summary-details {
    border-top: 1px solid #e2e7f1;
    padding-left: 0;
    padding-top: 1.25rem;
  }
}
@media (max-width: 575px) {
  .id-panel {
    padding: 1rem;
  }
  .id-summary-actions {
    align-items: flex-start;
    flex-direction: column;
  }
  .id-release-group {
    flex-wrap: wrap;
  }
}

/* Modal styles — prefixed to avoid Star Admin .close conflicts */
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
  max-width: 480px;
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

.image-preview-overlay {
  position: fixed;
  inset: 0;
  z-index: 1060;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  background: rgba(0, 0, 0, 0.92);
}
.image-preview-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
.image-preview-close {
  position: fixed;
  top: 1rem;
  right: 1rem;
  z-index: 1;
  width: 2.75rem;
  height: 2.75rem;
  border: 1px solid rgba(255, 255, 255, 0.35);
  border-radius: 50%;
  background: rgba(15, 23, 42, 0.6);
  color: #fff;
  cursor: pointer;
  font-size: 2rem;
  line-height: 1;
}
.image-preview-close:hover {
  background: rgba(15, 23, 42, 0.85);
}

/* Result toast */
.cd-toast {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  z-index: 99999;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  max-width: 360px;
  padding: 0.75rem 1rem;
  border-radius: 6px;
  color: #fff;
  font-size: 0.9rem;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
}
.cd-toast i {
  font-size: 1.2rem;
  flex-shrink: 0;
}
.cd-toast-success {
  background: #16a34a;
}
.cd-toast-error {
  background: #dc2626;
}
</style>
