<template>
  <div v-if="category && item">
    <!-- Breadcrumb -->
    <nav aria-label="breadcrumb">
      <ol class="breadcrumb bg-transparent p-0 mb-3">
        <li class="breadcrumb-item">
          <a href="#" @click.prevent="goToDashboard">{{ breadcrumbRoot }}</a>
        </li>
        <li class="breadcrumb-item">
          <router-link :to="`/categories/${category.id}`">{{ breadcrumbCategory }}</router-link>
        </li>
        <li class="breadcrumb-item active" aria-current="page">{{ breadcrumbItem }}</li>
      </ol>
    </nav>

    <div class="page-header">
      <h3 class="page-title">{{ itemPageTitle }}</h3>
    </div>

    <!-- Item name edit -->
    <div class="card mb-4">
      <div class="card-body">
        <h4 class="card-title">{{ itemNameLabel(contentLanguage) }}</h4>
        <div class="form-group mb-0">
          <input
            type="text"
            class="form-control"
            :value="item.name"
            @change="onNameChange"
          />
        </div>
      </div>
    </div>

    <!-- Files -->
    <div class="card mb-4">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-center mb-3">
          <h4 class="card-title mb-0">{{ filesLabel(contentLanguage) }}</h4>
          <div class="d-flex align-items-center" style="gap: 0.5rem;">
            <input
              ref="addFileInputRef"
              type="file"
              accept="image/*"
              class="d-none"
              aria-hidden="true"
              tabindex="-1"
              @change="onAddFilePicked"
            />
            <button v-if="hasFiles" type="button" class="btn btn-outline-primary btn-sm" @click="onRegenerateAllItemImages">
              <i class="mdi mdi-auto-fix mr-1"></i>Regenerate Images
            </button>
            <button v-if="hasFiles" type="button" class="btn btn-primary btn-sm" @click="onAddFile">
              <i class="mdi mdi-plus mr-1"></i>Add File
            </button>
          </div>
        </div>
        <input
          ref="replaceFileInputRef"
          type="file"
          accept="image/*"
          class="d-none"
          aria-hidden="true"
          tabindex="-1"
          @change="onReplaceFilePicked"
        />
        <div v-if="hasFiles" class="row">
          <div
            v-for="(file, index) in itemImages"
            :key="index"
            class="col-xl-4 col-lg-6 col-md-6 col-sm-12 col-12 mb-3"
          >
            <div class="file-card">
              <img
                :src="file"
                alt=""
                class="img-fluid rounded"
                style="width: 100%; aspect-ratio: 1/1; object-fit: cover;"
              />
              <div class="file-actions">
                <button
                  type="button"
                  class="btn-icon btn-icon-regenerate"
                  title="Regenerate"
                  aria-label="Regenerate image"
                  @click="onRegenerateFile(index)"
                >
                  <i class="mdi mdi-auto-fix"></i>
                </button>
                <button
                  type="button"
                  class="btn-icon btn-icon-edit"
                  title="Replace"
                  aria-label="Replace image"
                  @click="onReplaceFile(index)"
                >
                  <i class="mdi mdi-pencil"></i>
                </button>
                <button
                  type="button"
                  class="btn-icon btn-icon-delete"
                  title="Delete"
                  aria-label="Delete image"
                  @click="onDeleteFile(index)"
                >
                  <i class="mdi mdi-delete"></i>
                </button>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="file-empty">
          <div class="file-card file-card-empty">
            <img
              :src="cameraPlaceholderUrl"
              alt="No image available"
              class="img-fluid rounded"
              style="width: 100%; aspect-ratio: 1/1; object-fit: cover;"
            />
            <div class="file-actions">
              <button
                type="button"
                class="btn-icon btn-icon-regenerate"
                title="Regenerate"
                aria-label="Regenerate image"
                @click="onRegenerateAllItemImages"
              >
                <i class="mdi mdi-auto-fix"></i>
              </button>
              <button
                type="button"
                class="btn-icon btn-icon-edit"
                title="Upload"
                aria-label="Upload image"
                @click="onAddFile"
              >
                <i class="mdi mdi-pencil"></i>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Voices -->
    <div class="card mb-4">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-center mb-3">
          <h4 class="card-title mb-0">{{ voicesLabel(contentLanguage) }}</h4>
          <button type="button" class="btn btn-outline-primary btn-sm" @click="onRegenerateAllItemVoices">
            <i class="mdi mdi-auto-fix mr-1"></i>Regenerate Voices
          </button>
        </div>
        <input
          ref="voiceFileInputRef"
          type="file"
          accept="audio/*"
          class="d-none"
          aria-hidden="true"
          tabindex="-1"
          @change="onVoiceFilePicked"
        />
        <div class="table-responsive">
          <table class="table table-hover mb-0">
            <thead>
              <tr>
                <th>{{ languageColumnLabel(contentLanguage) }}</th>
                <th>{{ wordColumnLabel(contentLanguage) }}</th>
                <th>{{ typeColumnLabel(contentLanguage) }}</th>
                <th style="width: 150px;">{{ actionsColumnLabel(contentLanguage) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="voice in item.voices" :key="voice.id">
                <td>
                  <i :class="'flag-icon flag-icon-' + langFlag(voice.language)" style="margin-right: 8px;"></i>
                  {{ langLabel(voice.language) }}
                </td>
                <td>{{ voice.name }}</td>
                <td><span class="badge badge-light">{{ voice.type }}</span></td>
                <td>
                  <div class="item-voice-actions">
                    <button
                      class="voice-play-btn"
                      :class="{
                        'voice-play-btn-ready': hasAudio(voice) && playingVoiceId !== voice.id,
                        'voice-play-btn-playing': playingVoiceId === voice.id,
                        'voice-play-btn-disabled': !hasAudio(voice),
                      }"
                      :disabled="!hasAudio(voice)"
                      :aria-label="!hasAudio(voice) ? 'Voice file missing' : (playingVoiceId === voice.id ? 'Stop voice playback' : 'Play voice')"
                      :title="!hasAudio(voice) ? 'Voice file missing' : (playingVoiceId === voice.id ? 'Stop' : 'Play')"
                      @click="togglePlay(voice)"
                    >
                      <i :class="playingVoiceId === voice.id ? 'mdi mdi-stop' : 'mdi mdi-play'"></i>
                    </button>
                    <button
                      class="btn-icon btn-icon-regenerate item-voice-action"
                      type="button"
                      title="Regenerate voice"
                      aria-label="Regenerate voice"
                      @click="onRegenerateVoice(voice)"
                    >
                      <i class="mdi mdi-auto-fix"></i>
                    </button>
                    <button
                      class="btn-icon btn-icon-edit item-voice-action"
                      type="button"
                      title="Replace voice"
                      aria-label="Replace voice"
                      @click="onUploadVoice(voice)"
                    >
                      <i class="mdi mdi-pencil"></i>
                    </button>
                    <button
                      class="btn-icon btn-icon-delete item-voice-action"
                      type="button"
                      title="Delete or reset voice"
                      aria-label="Delete or reset voice"
                      @click="openVoiceDeleteModal(voice)"
                    >
                      <i class="mdi mdi-delete"></i>
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <div v-else class="text-center py-5">
    <h4 class="text-muted">Item not found</h4>
    <router-link :to="{ name: 'dashboard' }" class="btn btn-primary mt-3">Back to Categories</router-link>
  </div>

  <Teleport to="body">
    <div v-if="deleteConfirmVisible" class="cd-modal-overlay" @click.self="cancelDelete">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">Delete File</h5>
          <button type="button" class="cd-modal-close" @click="cancelDelete">&times;</button>
        </div>
        <div class="cd-modal-body">
          Are you sure you want to delete this file from "{{ category?.name }}"?
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="cancelDelete">Cancel</button>
          <button type="button" class="btn btn-danger" @click="confirmDelete">Delete</button>
        </div>
      </div>
    </div>
  </Teleport>

  <Teleport to="body">
    <div v-if="pendingNotice" class="id-toast">
      <i class="mdi mdi-information"></i>
      <span>{{ pendingNotice }}</span>
    </div>
  </Teleport>

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

  <Teleport to="body">
    <div v-if="voiceDeleteTarget" class="cd-modal-overlay" @click.self="cancelVoiceDelete">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">Delete or reset voice</h5>
          <button type="button" class="cd-modal-close" @click="cancelVoiceDelete">&times;</button>
        </div>
        <div class="cd-modal-body">
          Are you sure you want to delete or reset the voice for
          <strong>{{ voiceDeleteTarget.name }}</strong>
          (<span class="text-uppercase">{{ voiceDeleteTarget.language }}</span>)?
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="cancelVoiceDelete">Cancel</button>
          <button type="button" class="btn btn-danger" @click="confirmVoiceDelete">Delete</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { AxiosError } from 'axios'
import { ensureCategoriesLoaded, ensureCategoryDetail, ensureItemDetail, invalidateItemDetail, sharedCategories } from '../api/cache'
import {
  itemsUpdate,
  itemsImagesAdd,
  itemsImagesUpdate,
  itemsImagesDelete,
  itemsVoicesAdd,
  itemsVoicesUpdate,
  itemsVoicesDelete,
} from '../api/contentClient'
import { LocalizedString, type Language, type Voice } from '../types/viewModels'
import { useContentLanguage } from '../composables/useContentLanguage'
import { useAudioPlayer } from '../composables/useAudioPlayer'
import { resizeImageToMaxDimension } from '../utils/imageResize'
import { imageIdFromUri } from '../utils/imageId'
import cameraPlaceholderUrl from '../assets/images/samples/camera-placeholder.svg'
import {
  actionsColumnLabel,
  breadcrumbCategoriesLabel,
  filesLabel,
  itemNameLabel,
  languageColumnLabel,
  localizedCategoryLabel,
  localizedImageLabel,
  typeColumnLabel,
  voicesLabel,
  wordColumnLabel,
} from '../utils/localizedLabels'
import { toPascalCaseWords } from '../utils/pascalCaseWords'

const route = useRoute()
const router = useRouter()

const goToDashboard = (): void => {
  router.push({ name: 'dashboard' })
}

const contentLanguage = useContentLanguage()

onMounted(async () => {
  await ensureCategoriesLoaded()
  const categoryId = route.params.categoryId as string
  const itemId = route.params.itemId as string
  if (!categoryId || !itemId) return
  const cat = sharedCategories.value.find((c) => c.id.toLowerCase() === categoryId.toLowerCase())
  if (!cat) return
  await ensureCategoryDetail(cat.id, cat.name.en)
  await ensureItemDetail(cat.id, itemId)
})

watch(() => route.params.categoryId as string, async (id) => {
  if (!id) return
  const cat = sharedCategories.value.find((c) => c.id.toLowerCase() === id.toLowerCase())
  if (!cat) return
  await ensureCategoryDetail(cat.id, cat.name.en)
  const itemId = route.params.itemId as string
  if (itemId) await ensureItemDetail(cat.id, itemId)
})

watch(() => route.params.itemId as string, async (itemId) => {
  document.documentElement.scrollTop = 0
  document.body.scrollTop = 0
  if (!itemId || !category.value) return
  await ensureItemDetail(category.value.id, itemId)
})

watch(() => route.fullPath, () => {
  document.documentElement.scrollTop = 0
  document.body.scrollTop = 0
})

const category = computed(() => {
  const id = route.params.categoryId as string
  if (!id) return null
  return sharedCategories.value.find(
    (c) => c.id.toLowerCase() === id.toLowerCase(),
  ) ?? null
})

const item = computed(() => {
  const itemId = route.params.itemId as string
  return category.value?.items.find((entry) => entry.id === itemId) ?? null
})

// Cache-busting: file URIs are stable across replace (same filename), so the browser
// keeps serving the old cached file unless we force a fresh request.
const imageVersions = ref<Record<string, number>>({})
const voiceVersions = ref<Record<string, number>>({})

function cacheBustedUri(uri: string, version: number): string {
  if (!uri || !version) return uri
  const sep = uri.includes('?') ? '&' : '?'
  return `${uri}${sep}v=${version}`
}

const rawItemImages = computed(() => item.value?.images.filter((image) => image.trim().length > 0) ?? [])
const itemImages = computed(() =>
  rawItemImages.value.map((uri) => cacheBustedUri(uri, imageVersions.value[imageIdFromUri(uri)] ?? 0)),
)
const hasFiles = computed(() => itemImages.value.length > 0)

function voiceSrc(voice: Voice): string {
  return cacheBustedUri(voice.fileUri ?? '', voiceVersions.value[voice.id] ?? 0)
}

const { playingVoiceId, hasAudio, togglePlay, stopAudio } = useAudioPlayer(voiceSrc)

const breadcrumbRoot = computed(() => breadcrumbCategoriesLabel(contentLanguage.value))

const breadcrumbCategory = computed(() =>
  category.value ? localizedCategoryLabel(category.value, contentLanguage.value) : '',
)

const breadcrumbItem = computed(() =>
  item.value ? localizedImageLabel(item.value, contentLanguage.value) : '',
)

const itemPageTitle = computed(() => {
  if (!item.value) return ''
  return toPascalCaseWords(localizedImageLabel(item.value, contentLanguage.value))
})

const langFlagMap: Record<Language, string> = { en: 'us', es: 'es', fr: 'fr', bg: 'bg' }
const langLabelMap: Record<Language, string> = { en: 'English', es: 'Español', fr: 'Français', bg: 'Български' }

const langFlag = (lang: Language): string => langFlagMap[lang] || lang
const langLabel = (lang: Language): string => langLabelMap[lang] || lang

type ToastVariant = 'success' | 'error'
const toast = ref<{ message: string; variant: ToastVariant } | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showToast(message: string, variant: ToastVariant): void {
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

async function refreshItem(): Promise<void> {
  const cat = category.value
  const currentItem = item.value
  if (!cat || !currentItem) return
  invalidateItemDetail(cat.id, currentItem.id)
  await ensureItemDetail(cat.id, currentItem.id)
}

const onNameChange = async (e: Event): Promise<void> => {
  const val = (e.target as HTMLInputElement).value.trim()
  const cat = category.value
  const currentItem = item.value
  if (!cat || !currentItem || !val || val === currentItem.name) return

  try {
    await itemsUpdate(cat.id, currentItem.id, { item_name: new LocalizedString({ en: val }) })
    await refreshItem()
    showToast('Item name updated.', 'success')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}

const announceBackendPending = (message: string): void => {
  if (pendingNoticeTimer) clearTimeout(pendingNoticeTimer)
  pendingNotice.value = `Backend task pending: ${message}.`
  pendingNoticeTimer = setTimeout(() => { pendingNotice.value = null }, 4000)
  console.info(`Backend task pending: ${message}`)
}

const pendingNotice = ref<string | null>(null)
let pendingNoticeTimer: ReturnType<typeof setTimeout> | null = null

const addFileInputRef = ref<HTMLInputElement | null>(null)

const onAddFile = (): void => {
  addFileInputRef.value?.click()
}

const onAddFilePicked = async (e: Event): Promise<void> => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  const cat = category.value
  const currentItem = item.value
  if (!file || !cat || !currentItem) {
    input.value = ''
    return
  }

  try {
    const resizedFile = await resizeImageToMaxDimension(file)
    const formData = new FormData()
    formData.append('image', resizedFile)
    const result = await itemsImagesAdd(cat.id, currentItem.id, formData)
    await refreshItem()
    showToast(`Image ${pastTense(result.action)}.`, 'success')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  } finally {
    input.value = ''
  }
}
const replaceFileInputRef = ref<HTMLInputElement | null>(null)
const replaceFileIndex = ref<number>(-1)

const onReplaceFile = (index: number): void => {
  replaceFileIndex.value = index
  replaceFileInputRef.value?.click()
}

const onRegenerateFile = (index: number): void => {
  announceBackendPending(`regenerate image ${index + 1} for item ${item.value?.id ?? 'unknown item'}`)
}

const onRegenerateAllItemImages = (): void => {
  announceBackendPending(`regenerate images for item ${item.value?.id ?? 'unknown item'}`)
}

const onReplaceFilePicked = async (e: Event): Promise<void> => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  const cat = category.value
  const currentItem = item.value
  const rawUri = rawItemImages.value[replaceFileIndex.value]
  if (!file || !cat || !currentItem || !rawUri) {
    input.value = ''
    return
  }

  try {
    const imageId = imageIdFromUri(rawUri)
    const resizedFile = await resizeImageToMaxDimension(file)
    const formData = new FormData()
    formData.append('image', resizedFile)
    const result = await itemsImagesUpdate(cat.id, currentItem.id, imageId, formData)
    await refreshItem()
    imageVersions.value = { ...imageVersions.value, [imageId]: (imageVersions.value[imageId] ?? 0) + 1 }
    showToast(`Image ${pastTense(result.action)}.`, 'success')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  } finally {
    input.value = ''
  }
}
const deleteConfirmVisible = ref(false)
const deleteFileIndex = ref<number>(-1)

const onDeleteFile = (index: number): void => {
  deleteFileIndex.value = index
  deleteConfirmVisible.value = true
}

const cancelDelete = (): void => {
  deleteConfirmVisible.value = false
  deleteFileIndex.value = -1
}

const confirmDelete = async (): Promise<void> => {
  const cat = category.value
  const currentItem = item.value
  const rawUri = rawItemImages.value[deleteFileIndex.value]
  deleteConfirmVisible.value = false
  deleteFileIndex.value = -1
  if (!cat || !currentItem || !rawUri) return

  try {
    const result = await itemsImagesDelete(cat.id, currentItem.id, imageIdFromUri(rawUri))
    await refreshItem()
    showToast(`Image ${pastTense(result.action)}.`, 'success')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}

const voiceFileInputRef = ref<HTMLInputElement | null>(null)
const voiceUploadTarget = ref<Voice | null>(null)

const onUploadVoice = (voice: Voice): void => {
  voiceUploadTarget.value = voice
  voiceFileInputRef.value?.click()
}

const onVoiceFilePicked = async (e: Event): Promise<void> => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  const cat = category.value
  const currentItem = item.value
  const voice = voiceUploadTarget.value
  voiceUploadTarget.value = null
  if (!file || !cat || !currentItem || !voice) {
    input.value = ''
    return
  }

  const wasAdd = !hasAudio(voice)
  try {
    let result
    if (wasAdd) {
      const formData = new FormData()
      formData.append('language', voice.language)
      formData.append('audio', file)
      result = await itemsVoicesAdd(cat.id, currentItem.id, formData)
    } else {
      const formData = new FormData()
      formData.append('audio', file)
      result = await itemsVoicesUpdate(cat.id, currentItem.id, voice.id, formData)
    }
    await refreshItem()
    voiceVersions.value = { ...voiceVersions.value, [voice.id]: (voiceVersions.value[voice.id] ?? 0) + 1 }
    showToast(`Voice for ${voice.name} (${voice.language.toUpperCase()}) ${pastTense(result.action)}.`, 'success')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  } finally {
    input.value = ''
  }
}

const onRegenerateVoice = (voice: Voice): void => {
  announceBackendPending(`regenerate ${voice.language.toUpperCase()} voice ${voice.id} for item ${item.value?.id ?? 'unknown item'}`)
}

const onRegenerateAllItemVoices = (): void => {
  announceBackendPending(`regenerate voices for item ${item.value?.id ?? 'unknown item'}`)
}

const voiceDeleteTarget = ref<Voice | null>(null)

const openVoiceDeleteModal = (voice: Voice): void => {
  voiceDeleteTarget.value = voice
}

const cancelVoiceDelete = (): void => {
  voiceDeleteTarget.value = null
}

const confirmVoiceDelete = async (): Promise<void> => {
  const voice = voiceDeleteTarget.value
  const cat = category.value
  const currentItem = item.value
  voiceDeleteTarget.value = null
  if (!voice || !cat || !currentItem) return
  if (playingVoiceId.value === voice.id) stopAudio()

  try {
    const result = await itemsVoicesDelete(cat.id, currentItem.id, voice.id)
    await refreshItem()
    voiceVersions.value = { ...voiceVersions.value, [voice.id]: (voiceVersions.value[voice.id] ?? 0) + 1 }
    showToast(`Voice for ${voice.name} (${voice.language.toUpperCase()}) ${pastTense(result.action)}.`, 'success')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}

</script>

<style scoped>
.file-card {
  position: relative;
  border-radius: 6px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
}
.file-actions {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  gap: 4px;
  z-index: 2;
}
.file-empty {
  max-width: 320px;
}
.file-card-empty {
  background: #eff3f8;
}
.item-voice-actions {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.btn-icon {
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 15px;
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
.item-voice-action {
  width: 26px;
  height: 26px;
  font-size: 13px;
}
.voice-play-btn {
  width: 34px;
  height: 22px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
.voice-play-btn.voice-play-btn-ready {
  background: #047857 !important;
  border-color: #047857 !important;
  color: #fff !important;
}
.voice-play-btn.voice-play-btn-ready:hover,
.voice-play-btn.voice-play-btn-ready:focus {
  background: #065f46 !important;
  border-color: #065f46 !important;
  color: #fff !important;
}
.voice-play-btn.voice-play-btn-playing {
  background: #dc2626 !important;
  border-color: #dc2626 !important;
  color: #fff !important;
}
.voice-play-btn.voice-play-btn-playing:hover,
.voice-play-btn.voice-play-btn-playing:focus {
  background: #b91c1c !important;
  border-color: #b91c1c !important;
  color: #fff !important;
}
.voice-play-btn.voice-play-btn-disabled,
.voice-play-btn:disabled {
  background: #e5e7eb !important;
  border-color: #cbd5e1 !important;
  color: #64748b !important;
  cursor: not-allowed;
  opacity: 1;
}
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
.id-toast {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  z-index: 99999;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  max-width: 380px;
  padding: 0.75rem 1rem;
  border-radius: 6px;
  background: #0f766e;
  color: #fff;
  font-size: 0.9rem;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
}
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
