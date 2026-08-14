<template>
  <div class="card mb-2" style="width: 100%;">
    <div class="card-body">
      <!-- Row 1: name spans left; empty cell on md+ keeps column grid for row 2 -->
      <div class="row">
        <div class="col-lg-5 col-md-5 mb-2 mb-md-3">
          <label class="text-muted small mb-1 d-block">{{ categoryIdLabel(contentLanguage) }}</label>
          <ServerWord
            :text="category.id"
            :edit-title="renameCategoryLabel(contentLanguage)"
            @edit="openRenameModal"
          />
        </div>
        <div class="col-lg-7 col-md-7 d-flex justify-content-md-end align-items-start mb-2 mb-md-3">
          <button
            type="button"
            class="btn btn-outline-danger btn-sm"
            :disabled="archiving"
            @click="openArchiveModal"
          >
            <span v-if="archiving" class="spinner-border spinner-border-sm mr-1" role="status"></span>
            <i v-else class="mdi mdi-archive-arrow-down mr-1"></i>
            {{ archiveCategoryLabel(contentLanguage) }}
          </button>
        </div>
      </div>
      <!-- Row 2: "Category Icon" and "Category Voices" labels share one baseline -->
      <div class="row">
        <div class="col-lg-5 col-md-5 mb-3 mb-md-0">
          <div class="mb-3">
            <label class="text-muted small mb-1 d-block">{{ categoryIconLabel(contentLanguage) }}</label>
            <div class="category-icon-wrap">
              <img
                :src="thumbnailSrc"
                alt="Category icon"
                class="rounded"
              />
              <div class="category-icon-actions">
                <button
                  type="button"
                  class="btn-icon btn-icon-regenerate"
                  title="Regenerate category image"
                  @click="requestCategoryImageRegeneration"
                >
                  <i class="mdi mdi-auto-fix"></i>
                </button>
                <button
                  type="button"
                  class="btn-icon btn-icon-edit"
                  title="Upload category icon"
                  @click="openUploadModal"
                >
                  <i class="mdi mdi-pencil"></i>
                </button>
                <button
                  type="button"
                  class="btn-icon btn-icon-delete"
                  :title="deleteCategoryImageLabel(contentLanguage)"
                  :disabled="!category.thumbnail_uri"
                  @click="openDeleteModal"
                >
                  <i class="mdi mdi-delete"></i>
                </button>
              </div>
            </div>
          </div>
          <p class="text-muted mb-0" style="font-size: 0.9rem;">{{ category.description.get(contentLanguage) }}</p>
        </div>

        <div class="col-lg-7 col-md-7">
          <label class="text-muted small mb-1 d-block">{{ categoryVoicesLabel(contentLanguage) }}</label>
          <div class="voice-cards-grid">
            <div v-for="voice in category.voices" :key="voice.id" class="voice-card">
              <div class="voice-card-top-actions">
                <button
                  class="btn-icon btn-icon-regenerate voice-card-action"
                  type="button"
                  title="Regenerate voice"
                  aria-label="Regenerate voice"
                  @click="requestCategoryVoiceRegeneration(voice)"
                >
                  <i class="mdi mdi-auto-fix"></i>
                </button>
                <button
                  class="btn-icon btn-icon-edit voice-card-action"
                  type="button"
                  title="Upload voice"
                  aria-label="Upload voice"
                  @click="openVoiceUpload(voice)"
                >
                  <i class="mdi mdi-pencil"></i>
                </button>
                <button
                  type="button"
                  class="btn-icon btn-icon-delete voice-card-action"
                  :title="deleteOrResetVoiceLabel(voice.language)"
                  :aria-label="deleteOrResetVoiceLabel(voice.language)"
                  @click="openVoiceDeleteModal(voice)"
                >
                  <i class="mdi mdi-delete"></i>
                </button>
              </div>
              <div class="voice-card-flag">
                <i
                  :class="'flag-icon flag-icon-' + langFlag(voice.language)"
                ></i>
              </div>
              <ServerWord
                :text="voice.name"
                text-tag="div"
                text-class="voice-card-name"
                root-class="justify-content-center"
                :edit-title="editWordLabel(voice.language)"
                :icon-style="{ fontSize: '0.8rem' }"
                @edit="openVoiceWordModal(voice)"
              />
              <div class="voice-card-type">{{ voiceTypeLabel(voice.type) }}</div>
              <div class="voice-card-controls">
                <button
                  class="btn-play"
                  :class="{ playing: playingVoiceId === voice.id, disabled: !hasAudio(voice) }"
                  type="button"
                  :disabled="!hasAudio(voice)"
                  @click="togglePlay(voice)"
                  :title="!hasAudio(voice) ? 'Voice file missing' : (playingVoiceId === voice.id ? 'Stop' : 'Play')"
                >
                  <i :class="playingVoiceId === voice.id ? 'mdi mdi-stop-circle' : 'mdi mdi-play-circle'"></i>
                  <span>{{ playingVoiceId === voice.id ? 'Stop' : 'Play' }}</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

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
          <div class="alert alert-warning py-2">
            <small>
              <i class="mdi mdi-alert mr-1"></i>
              {{ archiveCategoryWarningLabel(contentLanguage, category.items_count ?? category.items.length) }}
            </small>
          </div>
          <div class="form-group mb-0">
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
          <button type="button" class="btn btn-secondary" @click="closeArchiveModal">{{ cancelLabel(contentLanguage) }}</button>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="!canArchive"
            @click="confirmArchive"
          >
            {{ archiveLabel(contentLanguage) }}
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
    <div v-if="showUpload" class="cd-modal-overlay" @click.self="showUpload = false">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ uploadCategoryIconLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="showUpload = false">&times;</button>
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
          <button type="button" class="btn btn-secondary" @click="showUpload = false">{{ cancelLabel(contentLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Edit voice word (label) modal -->
  <Teleport to="body">
    <div v-if="showVoiceWordEdit" class="cd-modal-overlay" @click.self="closeVoiceWordEdit">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ editWordLabel(voiceWordEditLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeVoiceWordEdit">&times;</button>
        </div>
        <div class="cd-modal-body">
          <p v-if="voiceWordEditTarget" class="text-muted small mb-2">
            <i :class="'flag-icon flag-icon-' + langFlag(voiceWordEditLanguage)" class="mr-1"></i>
            {{ voiceWordEditLanguage.toUpperCase() }}
          </p>
          <div class="form-group mb-0">
            <label class="col-form-label">{{ wordColumnLabel(voiceWordEditLanguage) }}</label>
            <input
              v-model="voiceWordValue"
              type="text"
              class="form-control"
              :placeholder="enterWordLabel(voiceWordEditLanguage)"
              @keyup.enter="confirmVoiceWordEdit"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeVoiceWordEdit">{{ cancelLabel(voiceWordEditLanguage) }}</button>
          <button type="button" class="btn btn-primary" @click="confirmVoiceWordEdit">{{ saveLabel(voiceWordEditLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Delete / reset voice confirmation -->
  <Teleport to="body">
    <div v-if="showVoiceDeleteConfirm" class="cd-modal-overlay" @click.self="closeVoiceDeleteModal">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ deleteOrResetVoiceLabel(voiceDeleteLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeVoiceDeleteModal">&times;</button>
        </div>
        <div class="cd-modal-body">
          <template v-if="voiceDeleteTarget">
            <p class="mb-2">
              {{ deleteOrResetVoiceConfirmLabel(voiceDeleteLanguage, voiceDeleteTarget.name, voiceDeleteTarget.language) }}
            </p>
            <p class="text-muted small mb-0">
              {{ deleteOrResetVoiceHelpLabel(voiceDeleteLanguage) }}
            </p>
          </template>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeVoiceDeleteModal">{{ cancelLabel(voiceDeleteLanguage) }}</button>
          <button type="button" class="btn btn-danger" @click="confirmVoiceDelete">{{ deleteResetLabel(voiceDeleteLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Upload voice file modal -->
  <Teleport to="body">
    <div v-if="showVoiceUpload" class="cd-modal-overlay" @click.self="closeVoiceUpload">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ uploadVoiceFileLabel(uploadVoiceLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeVoiceUpload">&times;</button>
        </div>
        <div class="cd-modal-body">
          <p v-if="editingVoice" class="text-muted small mb-2">{{ editingVoice.name }} ({{ editingVoice.language }})</p>
          <div class="form-group mb-0">
            <label class="col-form-label">{{ audioFileLabel(uploadVoiceLanguage) }}</label>
            <input
              type="file"
              class="form-control-file"
              accept="audio/*"
              @change="onVoiceFileSelected"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeVoiceUpload">{{ cancelLabel(uploadVoiceLanguage) }}</button>
        </div>
      </div>
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
import { ref, computed } from 'vue'
import type { AxiosError } from 'axios'
import ServerWord from './ServerWord.vue'
import type { CategoryFull, Language, Voice } from '../types/viewModels'
import {
  archiveCategoryConfirmLabel,
  archiveCategoryLabel,
  archiveCategoryWarningLabel,
  archiveLabel,
  archiveTypeToConfirmLabel,
  audioFileLabel,
  cancelLabel,
  categoryIdLabel,
  categoryIconLabel,
  categoryVoicesLabel,
  deleteCategoryImageConfirmLabel,
  deleteCategoryImageLabel,
  deleteLabel,
  deleteOrResetVoiceConfirmLabel,
  deleteOrResetVoiceHelpLabel,
  deleteOrResetVoiceLabel,
  deleteResetLabel,
  editWordLabel,
  enterCategoryIdLabel,
  enterWordLabel,
  imageFileLabel,
  localizedCategoryLabel,
  renameCategoryLabel,
  saveLabel,
  uploadCategoryIconLabel,
  uploadVoiceFileLabel,
  wordColumnLabel,
} from '../utils/localizedLabels'
import { useContentLanguage } from '../composables/useContentLanguage'
import {
  categoriesImagesAdd,
  categoriesImagesDelete,
  categoriesImagesUpdate,
  categoriesVoicesAdd,
  categoriesVoicesDelete,
  categoriesVoicesUpdate,
} from '../api/contentClient'
import { ensureCategoryDetail, invalidateCategoryDetail } from '../api/cache'
import { resizeImageToMaxDimension } from '../utils/imageResize'
import { imageIdFromUri } from '../utils/imageId'
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
  'edit-voice': [voice: Voice, action: 'add' | 'update']
  'delete-voice': [voice: Voice]
  'rename-voice': [voice: Voice, name: string]
}>()

const langFlagMap: Record<Language, string> = { en: 'us', es: 'es', fr: 'fr', bg: 'bg' }
const langFlag = (lang: Language): string => langFlagMap[lang] || lang

const voiceTypeLabelMap: Record<string, string> = {
  male: 'Narrator',
  female: 'Voiceover Artist',
  ambient: 'Ambient',
}
const voiceTypeLabel = (type: string): string => voiceTypeLabelMap[type] || type

const showDelete = ref(false)
const showUpload = ref(false)
const showRename = ref(false)
const showVoiceUpload = ref(false)
const showVoiceWordEdit = ref(false)
const voiceWordEditTarget = ref<Voice | null>(null)
const voiceWordValue = ref('')
const voiceWordEditLanguage = computed<Language>(() => voiceWordEditTarget.value?.language ?? contentLanguage.value)
const showArchive = ref(false)
const archiveConfirmText = ref('')
const showVoiceDeleteConfirm = ref(false)
const voiceDeleteTarget = ref<Voice | null>(null)
const voiceDeleteLanguage = computed<Language>(() => voiceDeleteTarget.value?.language ?? contentLanguage.value)
const editingVoice = ref<Voice | null>(null)
const uploadVoiceLanguage = computed<Language>(() => editingVoice.value?.language ?? contentLanguage.value)
const renameValue = ref('')

type ToastVariant = 'success' | 'error'
const toast = ref<{ message: string; variant: ToastVariant } | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | null = null

// Cache-busting: file URIs are stable across re-uploads (same filename), so the
// browser keeps serving the old cached file unless we force a fresh request.
const thumbnailVersion = ref(0)
const voiceVersions = ref<Record<string, number>>({})

function cacheBustedUri(uri: string | null | undefined, version: number): string {
  if (!uri) return ''
  if (!version) return uri
  const sep = uri.includes('?') ? '&' : '?'
  return `${uri}${sep}v=${version}`
}

const thumbnailSrc = computed(() => cacheBustedUri(props.category.thumbnail_uri, thumbnailVersion.value))

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
  showVoiceUpload.value = false
  showVoiceWordEdit.value = false
  showVoiceDeleteConfirm.value = false
  editingVoice.value = null
  voiceWordEditTarget.value = null
  voiceDeleteTarget.value = null
}

/**
 * Archiving is only recoverable when ARCHIVE_DIR is configured on the server, which the
 * frontend cannot detect, so the id must be typed out before the action is enabled.
 */
const canArchive = computed(() => archiveConfirmText.value.trim() === props.category.id)

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

function openVoiceDeleteModal(voice: Voice): void {
  closeAllModals()
  voiceDeleteTarget.value = voice
  showVoiceDeleteConfirm.value = true
}

function closeVoiceDeleteModal(): void {
  showVoiceDeleteConfirm.value = false
  voiceDeleteTarget.value = null
}

async function confirmVoiceDelete(): Promise<void> {
  const voice = voiceDeleteTarget.value
  showVoiceDeleteConfirm.value = false
  voiceDeleteTarget.value = null
  if (!voice) return
  if (playingVoiceId.value === voice.id) stopAudio()

  try {
    const result = await categoriesVoicesDelete(props.category.id, voice.id)
    showToast(`Voice for ${voice.name} (${voice.language.toUpperCase()}) ${pastTense(result.action)}.`, 'success')
    await refreshCategory()
    voiceVersions.value = { ...voiceVersions.value, [voice.id]: (voiceVersions.value[voice.id] ?? 0) + 1 }
    emit('delete-voice', voice)
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}

function openVoiceWordModal(voice: Voice): void {
  closeAllModals()
  voiceWordEditTarget.value = voice
  voiceWordValue.value = voice.name
  showVoiceWordEdit.value = true
}

function closeVoiceWordEdit(): void {
  showVoiceWordEdit.value = false
  voiceWordEditTarget.value = null
}

function confirmVoiceWordEdit(): void {
  const voice = voiceWordEditTarget.value
  const trimmed = voiceWordValue.value.trim()
  showVoiceWordEdit.value = false
  voiceWordEditTarget.value = null
  if (voice && trimmed && trimmed !== voice.name) {
    emit('rename-voice', voice, trimmed)
  }
}

function openUploadModal(): void {
  closeAllModals()
  showUpload.value = true
}

function openDeleteModal(): void {
  closeAllModals()
  showDelete.value = true
}

function requestCategoryImageRegeneration(): void {
  showToast('Backend task pending: regenerate the category image from the saved category metadata.', 'error')
}

function requestCategoryVoiceRegeneration(voice: Voice): void {
  showToast(`Backend task pending: regenerate ${voice.language.toUpperCase()} audio for ${voice.name}.`, 'error')
}

async function confirmImageDelete(): Promise<void> {
  showDelete.value = false
  const uri = props.category.thumbnail_uri
  if (!uri || !uri.trim()) return

  try {
    const result = await categoriesImagesDelete(props.category.id, imageIdFromUri(uri))
    showToast(`Category icon ${pastTense(result.action)}.`, 'success')
    await refreshCategory()
    thumbnailVersion.value += 1
    emit('delete')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}

function openVoiceUpload(voice: Voice): void {
  closeAllModals()
  editingVoice.value = voice
  showVoiceUpload.value = true
}

function closeVoiceUpload(): void {
  showVoiceUpload.value = false
  editingVoice.value = null
}

async function onVoiceFileSelected(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  const voice = editingVoice.value
  closeVoiceUpload()
  if (!file || !voice) return

  const wasAdd = !hasAudio(voice)
  try {
    let result
    if (wasAdd) {
      const formData = new FormData()
      formData.append('language', voice.language)
      formData.append('audio', file)
      result = await categoriesVoicesAdd(props.category.id, formData)
    } else {
      const formData = new FormData()
      formData.append('audio', file)
      result = await categoriesVoicesUpdate(props.category.id, voice.id, formData)
    }
    showToast(`Voice for ${voice.name} (${voice.language.toUpperCase()}) ${pastTense(result.action)}.`, 'success')
    await refreshCategory()
    voiceVersions.value = { ...voiceVersions.value, [voice.id]: (voiceVersions.value[voice.id] ?? 0) + 1 }
    emit('edit-voice', voice, wasAdd ? 'add' : 'update')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  } finally {
    input.value = ''
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
  showUpload.value = false
  if (!file) return

  const formData = new FormData()
  try {
    const resizedFile = await resizeImageToMaxDimension(file)
    formData.append('image', resizedFile)
    const existingUri = props.category.thumbnail_uri
    const result = existingUri && existingUri.trim()
      ? await categoriesImagesUpdate(props.category.id, imageIdFromUri(existingUri), formData)
      : await categoriesImagesAdd(props.category.id, formData)
    showToast(`Category icon ${pastTense(result.action)}.`, 'success')
    await refreshCategory()
    thumbnailVersion.value += 1
    emit('edit')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  } finally {
    input.value = ''
  }
}

</script>

<style scoped>
/* Category icon */
.category-icon-wrap {
  position: relative;
  width: 100%;
  max-width: 512px;
  aspect-ratio: 1 / 1;
  background: rgba(0, 0, 0, 0.04);
}
.category-icon-wrap img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  object-position: center;
  display: block;
}
.category-icon-actions {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  gap: 4px;
  z-index: 2;
}
.btn-icon {
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 15px;
  color: #fff;
  padding: 0;
}
.btn-icon-edit {
  background: #4f46e5;
}
.btn-icon-edit:hover {
  background: #4338ca;
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

/* Voice cards: wrap to extra rows when the column is narrow (was fixed 4 columns). */
.voice-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(7.5rem, 1fr));
  gap: 0.75rem;
}
.voice-card {
  position: relative;
  min-width: 0;
  border: 1.5px solid #b8d4f0;
  border-radius: 10px;
  padding: 2.15rem 0.75rem 0.75rem;
  text-align: center;
  background: #f8fbff;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.voice-card-top-actions {
  position: absolute;
  top: 6px;
  right: 6px;
  display: flex;
  gap: 0.3rem;
}
.voice-card-flag {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: #e8f0fe;
  border: 2px solid #d1e3f6;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 0.5rem;
}
.voice-card-flag .flag-icon {
  font-size: 1.6rem;
}
:deep(.voice-card-name) {
  font-weight: 600;
  font-size: 0.85rem;
  color: #1e293b;
  margin-bottom: 2px;
  line-height: 1.2;
}
.voice-card-type {
  font-size: 0.75rem;
  color: #6b7280;
  margin-bottom: 0.5rem;
}
.voice-card-controls {
  margin-bottom: 0.5rem;
}
.btn-play {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  background: none;
  border: 1px solid #4f46e5;
  border-radius: 4px;
  color: #4f46e5;
  cursor: pointer;
  font-size: 0.78rem;
  padding: 0.15rem 0.5rem;
  transition: all 0.15s;
}
.btn-play i {
  font-size: 1rem;
}
.btn-play:hover {
  background: #4f46e5;
  color: #fff;
}
.btn-play.playing {
  border-color: #dc2626;
  color: #dc2626;
}
.btn-play.playing:hover {
  background: #dc2626;
  color: #fff;
}
.btn-play.disabled,
.btn-play:disabled {
  border-color: #d1d5db;
  color: #9ca3af;
  cursor: not-allowed;
  opacity: 0.7;
}
.btn-play.disabled:hover,
.btn-play:disabled:hover {
  background: transparent;
  color: #9ca3af;
}
.voice-card-action {
  width: 26px;
  height: 26px;
  font-size: 13px;
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
