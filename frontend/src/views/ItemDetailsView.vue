<template>
  <div v-if="category && item" class="item-detail-page">
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

    <section class="id-panel id-summary-panel">
      <div class="id-summary-copy">
        <div>
          <div class="id-field-label">Item Id</div>
          <div class="id-value-row">
            <h3 class="id-item-id">{{ item.id }}</h3>
            <button type="button" class="id-edit-btn" title="Edit item id" aria-label="Edit item id" @click="onEditItemId">
              <i class="mdi mdi-pencil"></i>
            </button>
          </div>
        </div>

        <div>
          <div class="id-field-label">Current Image</div>
          <div
            v-if="currentImageCard"
            class="id-summary-image-preview id-image-preview-trigger"
            role="button"
            tabindex="0"
            :aria-label="`Open ${currentImageCard.title} preview`"
            @click="openImagePreview(currentImageCard.src, currentImageCard.title)"
            @keydown.enter.prevent="openImagePreview(currentImageCard.src, currentImageCard.title)"
            @keydown.space.prevent="openImagePreview(currentImageCard.src, currentImageCard.title)"
          >
            <img :src="currentImageCard.src" :alt="currentImageCard.title" class="id-summary-image" />
          </div>
          <div v-else class="id-summary-image-preview">
            <img :src="cameraPlaceholderUrl" alt="No image available" class="id-summary-image" />
          </div>
        </div>

        <div>
          <div class="id-field-label">Description</div>
          <p class="id-description">{{ categoryDescriptionText }}</p>
        </div>
      </div>

      <div class="id-summary-details">
        <div class="id-summary-actions id-summary-actions-only">
          <div class="id-release-group">
            <button type="button" class="btn btn-outline-primary btn-sm" @click="onReleaseItem">
              Release Item
              <i class="mdi mdi-chevron-down ml-1"></i>
            </button>
            <button type="button" class="btn btn-outline-primary btn-sm id-square-btn" title="Item analytics" aria-label="Item analytics" @click="onItemAnalytics">
              <i class="mdi mdi-chart-line"></i>
            </button>
          </div>
        </div>

        <div class="id-summary-voices-panel">
          <h4 class="id-section-kicker">
            Item Voices
            <span>({{ item.voices.length }})</span>
          </h4>
          <div class="id-summary-voice-cards-grid">
            <article v-for="voice in itemVoiceCards" :key="voice.id" class="id-summary-voice-card">
              <div class="id-summary-voice-actions">
                <button class="btn-icon btn-icon-release id-summary-voice-action" type="button" title="Release voice" aria-label="Release voice" @click="onReleaseVoice(voice)">
                  <i class="mdi mdi-upload"></i>
                </button>
                <button class="btn-icon btn-icon-edit id-summary-voice-action" type="button" title="Replace voice" aria-label="Replace voice" @click="onUploadVoice(voice)">
                  <i class="mdi mdi-pencil"></i>
                </button>
                <button class="btn-icon btn-icon-delete id-summary-voice-action" type="button" title="Delete or reset voice" aria-label="Delete or reset voice" @click="openVoiceDeleteModal(voice)">
                  <i class="mdi mdi-delete"></i>
                </button>
              </div>
              <div class="id-summary-voice-flag" :title="langLabel(voice.language)" :aria-label="langLabel(voice.language)">
                <i :class="'flag-icon flag-icon-' + langFlag(voice.language)"></i>
              </div>
              <div class="id-summary-voice-body">
                <p class="id-summary-voice-name">{{ voice.name }}</p>
                <p class="id-summary-voice-type">{{ voiceTypeLabel(voice.type) }}</p>
              </div>
              <button class="btn-play" :class="{ playing: playingVoiceId === voice.id, disabled: !hasAudio(voice) }" type="button" :disabled="!hasAudio(voice)" :aria-label="!hasAudio(voice) ? 'Voice file missing' : (playingVoiceId === voice.id ? 'Stop voice playback' : 'Play voice')" :title="!hasAudio(voice) ? 'Voice file missing' : (playingVoiceId === voice.id ? 'Stop' : 'Play')" @click="togglePlay(voice)">
                <i :class="playingVoiceId === voice.id ? 'mdi mdi-stop-circle' : 'mdi mdi-play-circle'"></i>
                <span>{{ playingVoiceId === voice.id ? 'Stop' : 'Play' }}</span>
              </button>
            </article>
          </div>
        </div>
      </div>
    </section>

    <input ref="addFileInputRef" type="file" accept="image/*" class="d-none" aria-hidden="true" tabindex="-1" @change="onAddFilePicked" />
    <input ref="replaceFileInputRef" type="file" accept="image/*" class="d-none" aria-hidden="true" tabindex="-1" @change="onReplaceFilePicked" />

    <section class="id-panel">
      <div class="id-section-header">
        <h4 class="id-section-title">
          Images
          <span>({{ itemImages.length }})</span>
        </h4>
        <div class="id-toolbar">
          <button type="button" class="btn btn-outline-primary btn-sm" @click="onAddFile">
            <i class="mdi mdi-plus mr-1"></i>Add Images
          </button>
          <button v-if="hasFiles" type="button" class="btn btn-primary btn-sm" @click="onReleaseAllItemImages">Release All Images</button>
        </div>
      </div>

      <div v-if="hasFiles" class="id-image-grid">
        <div v-for="card in imageCards" :key="card.index" class="id-image-card">
          <div
            class="id-image-preview id-image-preview-trigger"
            role="button"
            tabindex="0"
            :aria-label="`Open ${card.title} preview`"
            @click="openImagePreview(card.src, card.title)"
            @keydown.enter.prevent="openImagePreview(card.src, card.title)"
            @keydown.space.prevent="openImagePreview(card.src, card.title)"
          >
            <img :src="card.src" :alt="card.title" class="id-image" />
            <div class="file-actions" @click.stop>
              <button type="button" class="btn-icon btn-icon-edit" title="Replace" aria-label="Replace image" @click.stop="onReplaceFile(card.index)">
                <i class="mdi mdi-pencil"></i>
              </button>
              <button type="button" class="btn-icon btn-icon-delete" title="Delete" aria-label="Delete image" @click.stop="onDeleteFile(card.index)">
                <i class="mdi mdi-delete"></i>
              </button>
            </div>
          </div>
          <div class="id-image-meta">
            <div class="id-meta-label">Generator</div>
            <strong>{{ card.generator }}</strong>
            <div class="id-meta-label id-meta-spaced">Prompt</div>
            <p>{{ card.prompt }}</p>
            <button type="button" class="id-release-btn" :class="{ 'id-release-btn-active': card.isSelected }" :disabled="card.isSelected" @click="onSelectImage(card.index)">
              <i :class="card.isSelected ? 'mdi mdi-check' : 'mdi mdi-image'"></i>
              {{ card.isSelected ? 'Selected' : 'Select Image' }}
            </button>
          </div>
        </div>
      </div>
      <div v-else class="file-empty">
        <div class="id-image-card id-image-card-empty">
          <div class="id-image-preview">
            <img :src="cameraPlaceholderUrl" alt="No image available" class="id-image" />
            <div class="file-actions">
              <button type="button" class="btn-icon btn-icon-edit" title="Upload" aria-label="Upload image" @click="onAddFile">
                <i class="mdi mdi-pencil"></i>
              </button>
            </div>
          </div>
          <div class="id-image-meta">
            <div class="id-meta-label">Generator</div>
            <strong>No image</strong>
            <div class="id-meta-label id-meta-spaced">Prompt</div>
            <p>Add an image variant for this item.</p>
            <button type="button" class="id-release-btn" @click="onAddFile">
              <i class="mdi mdi-plus"></i>
              Add Image
            </button>
          </div>
        </div>
      </div>
    </section>

    <section class="id-panel">
      <div class="id-section-header">
        <h4 class="id-section-title">
          Voices
          <span>({{ item.voices.length }})</span>
        </h4>
        <div class="id-toolbar">
          <button type="button" class="btn btn-outline-primary btn-sm" @click="onAddVoice">
            <i class="mdi mdi-plus mr-1"></i>Add Voice
          </button>
          <button type="button" class="btn btn-primary btn-sm" @click="onReleaseAllItemVoices">Release All Voices</button>
        </div>
      </div>
      <input ref="voiceFileInputRef" type="file" accept="audio/*" class="d-none" aria-hidden="true" tabindex="-1" @change="onVoiceFilePicked" />
      <div class="table-responsive id-voices-table-wrap">
        <table class="table id-voices-table mb-0">
          <thead>
            <tr>
              <th>Language</th>
              <th>Generator</th>
              <th>File (Normal)</th>
              <th>Preview</th>
              <th>File (Optimized)</th>
              <th>Preview</th>
              <th class="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="group in groupedVoices" :key="group.language">
              <tr v-for="(voice, index) in group.voices" :key="voice.id">
                <td>
                  <div v-if="index === 0" class="id-voice-language">
                    <i class="mdi mdi-chevron-right"></i>
                    <i :class="'flag-icon flag-icon-' + langFlag(group.language)"></i>
                    <span>{{ langLabel(group.language) }}</span>
                    <span class="id-count-pill">{{ group.voices.length }}</span>
                  </div>
                </td>
                <td>{{ voice.type }}</td>
                <td :title="voiceFileName(voice)">{{ voiceFileName(voice) }}</td>
                <td>
                  <div class="id-audio-preview">
                    <button class="id-audio-play" :class="{ 'id-audio-play-active': playingVoiceId === voice.id }" :disabled="!hasAudio(voice)" :aria-label="!hasAudio(voice) ? 'Voice file missing' : (playingVoiceId === voice.id ? 'Stop voice playback' : 'Play voice')" :title="!hasAudio(voice) ? 'Voice file missing' : (playingVoiceId === voice.id ? 'Stop' : 'Play')" @click="togglePlay(voice)">
                      <i :class="playingVoiceId === voice.id ? 'mdi mdi-stop' : 'mdi mdi-play'"></i>
                    </button>
                  </div>
                </td>
                <td :title="optimizedVoiceFileName(voice)">{{ optimizedVoiceFileName(voice) }}</td>
                <td>
                  <div class="id-audio-preview">
                    <button class="id-audio-play" :class="{ 'id-audio-play-active': playingVoiceId === voice.id }" :disabled="!hasAudio(voice)" :aria-label="!hasAudio(voice) ? 'Voice file missing' : (playingVoiceId === voice.id ? 'Stop optimized voice playback' : 'Play optimized voice')" :title="!hasAudio(voice) ? 'Voice file missing' : (playingVoiceId === voice.id ? 'Stop' : 'Play')" @click="togglePlay(voice)">
                      <i :class="playingVoiceId === voice.id ? 'mdi mdi-stop' : 'mdi mdi-play'"></i>
                    </button>
                  </div>
                </td>
                <td>
                  <div class="item-voice-actions">
                    <button class="id-action-square id-action-release" type="button" title="Release voice" aria-label="Release voice" @click="onReleaseVoice(voice)">
                      <i class="mdi mdi-upload"></i>
                    </button>
                    <button class="id-action-square id-action-edit" type="button" title="Replace voice" aria-label="Replace voice" @click="onUploadVoice(voice)">
                      <i class="mdi mdi-pencil"></i>
                    </button>
                    <button class="id-action-square id-action-delete" type="button" title="Delete or reset voice" aria-label="Delete or reset voice" @click="openVoiceDeleteModal(voice)">
                      <i class="mdi mdi-delete"></i>
                    </button>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>
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
    <div v-if="showItemIdEdit" class="cd-modal-overlay" @click.self="closeItemIdEdit">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">Change Item Id</h5>
          <button type="button" class="cd-modal-close" @click="closeItemIdEdit">&times;</button>
        </div>
        <div class="cd-modal-body">
          <div class="form-group mb-0">
            <label class="col-form-label">New Item Id</label>
            <input
              v-model="itemIdEditValue"
              type="text"
              class="form-control"
              placeholder="Enter item id"
              @keyup.enter="confirmItemIdEdit"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeItemIdEdit">Cancel</button>
          <button type="button" class="btn btn-primary" @click="confirmItemIdEdit">Save</button>
        </div>
      </div>
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

  <Teleport to="body">
    <div v-if="imagePreview" class="image-preview-overlay" @click.self="closeImagePreview">
      <button
        type="button"
        class="image-preview-close"
        aria-label="Close image preview"
        @click="closeImagePreview"
      >
        &times;
      </button>
      <img :src="imagePreview.src" :alt="imagePreview.title" class="image-preview-image" />
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { AxiosError } from 'axios'
import { ensureCategoriesLoaded, ensureCategoryDetail, ensureItemDetail, invalidateItemDetail, sharedCategories } from '../api/cache'
import {
  itemsImagesAdd,
  itemsImagesUpdate,
  itemsImagesDelete,
  itemsVoicesAdd,
  itemsVoicesUpdate,
  itemsVoicesDelete,
} from '../api/contentClient'
import { SUPPORTED_LANGUAGES, type Language, type Voice } from '../types/viewModels'
import { useContentLanguage } from '../composables/useContentLanguage'
import { useAudioPlayer } from '../composables/useAudioPlayer'
import { resizeImageToMaxDimension } from '../utils/imageResize'
import { imageIdFromUri } from '../utils/imageId'
import cameraPlaceholderUrl from '../assets/images/samples/camera-placeholder.svg'
import {
  breadcrumbCategoriesLabel,
  localizedCategoryLabel,
  localizedImageLabel,
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
const selectedImageIndex = ref(0)

watch(() => item.value?.id, () => {
  selectedImageIndex.value = 0
})

watch(() => itemImages.value.length, (length) => {
  if (selectedImageIndex.value >= length) selectedImageIndex.value = 0
})

function voiceSrc(voice: Voice): string {
  return cacheBustedUri(voice.fileUri ?? '', voiceVersions.value[voice.id] ?? 0)
}

const {
  playingVoiceId,
  hasAudio,
  togglePlay,
  stopAudio,
} = useAudioPlayer(voiceSrc)

const breadcrumbRoot = computed(() => breadcrumbCategoriesLabel(contentLanguage.value))

const breadcrumbCategory = computed(() =>
  category.value ? toPascalCaseWords(localizedCategoryLabel(category.value, contentLanguage.value)) : '',
)

const breadcrumbItem = computed(() =>
  item.value ? toPascalCaseWords(localizedImageLabel(item.value, contentLanguage.value)) : '',
)

const langFlagMap: Record<Language, string> = { en: 'us', es: 'es', fr: 'fr', bg: 'bg' }
const langLabelMap: Record<Language, string> = { en: 'English', es: 'Español', fr: 'Français', bg: 'Български' }

const langFlag = (lang: Language): string => langFlagMap[lang] || lang
const langLabel = (lang: Language): string => langLabelMap[lang] || lang

const categoryDescriptionText = computed(() => {
  const currentCategory = category.value
  if (!currentCategory) return ''
  const localized = currentCategory.description.get(contentLanguage.value, currentCategory.description.en).trim()
  return localized || currentCategory.description.en || 'No description available.'
})

function fileNameFromUri(uri?: string | null): string {
  if (!uri) return 'No file'
  const cleanUri = uri.split('?')[0] ?? uri
  const fileName = cleanUri.split('/').filter(Boolean).pop()
  return fileName ? decodeURIComponent(fileName) : 'No file'
}

const imageCards = computed(() =>
  itemImages.value.map((src, index) => {
    const sourceFile = fileNameFromUri(rawItemImages.value[index])
    return {
      src,
      index,
      isSelected: index === selectedImageIndex.value,
      title: `${breadcrumbItem.value} image ${index + 1}`,
      generator: index === 0 ? 'Main Item Image' : `Image Variant ${index + 1}`,
      prompt: `Source file: ${sourceFile}`,
    }
  }),
)

const currentImageCard = computed(() => imageCards.value[selectedImageIndex.value] ?? imageCards.value[0] ?? null)

const groupedVoices = computed(() =>
  SUPPORTED_LANGUAGES
    .map((language) => ({
      language,
      voices: item.value?.voices.filter((voice) => voice.language === language) ?? [],
    }))
    .filter((group) => group.voices.length > 0),
)

const itemVoiceCards = computed(() =>
  SUPPORTED_LANGUAGES.flatMap((language) =>
    item.value?.voices.filter((voice) => voice.language === language) ?? [],
  ),
)

const voiceTypeLabelMap: Record<string, string> = {
  male: 'Narrator',
  female: 'Voiceover Artist',
  ambient: 'Ambient',
}
const voiceTypeLabel = (type: string): string => voiceTypeLabelMap[type] || type

function voiceFileName(voice: Voice): string {
  return fileNameFromUri(voice.fileUri)
}

function optimizedVoiceFileName(voice: Voice): string {
  const fileName = voiceFileName(voice)
  if (fileName === 'No file') return fileName
  const dotIndex = fileName.lastIndexOf('.')
  if (dotIndex <= 0) return `${fileName}_optimized`
  return `${fileName.slice(0, dotIndex)}_optimized${fileName.slice(dotIndex)}`
}

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

const announceBackendPending = (message: string): void => {
  if (pendingNoticeTimer) clearTimeout(pendingNoticeTimer)
  pendingNotice.value = `Backend task pending: ${message}.`
  pendingNoticeTimer = setTimeout(() => { pendingNotice.value = null }, 4000)
  console.info(`Backend task pending: ${message}`)
}

const pendingNotice = ref<string | null>(null)
let pendingNoticeTimer: ReturnType<typeof setTimeout> | null = null
const showItemIdEdit = ref(false)
const itemIdEditValue = ref('')

const onEditItemId = (): void => {
  itemIdEditValue.value = item.value?.id ?? ''
  showItemIdEdit.value = true
}

const closeItemIdEdit = (): void => {
  showItemIdEdit.value = false
}

const confirmItemIdEdit = (): void => {
  const trimmed = itemIdEditValue.value.trim()
  const currentId = item.value?.id ?? ''
  showItemIdEdit.value = false
  if (trimmed && trimmed !== currentId) {
    announceBackendPending(`rename item id ${currentId} to ${trimmed}`)
  }
}

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

const onReleaseItem = (): void => {
  announceBackendPending(`release item ${item.value?.id ?? 'unknown item'}`)
}

const onItemAnalytics = (): void => {
  announceBackendPending(`open analytics for item ${item.value?.id ?? 'unknown item'}`)
}

const onSelectImage = (index: number): void => {
  selectedImageIndex.value = index
}

const onReleaseAllItemImages = (): void => {
  announceBackendPending(`release images for item ${item.value?.id ?? 'unknown item'}`)
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
const imagePreview = ref<{ src: string; title: string } | null>(null)

function openImagePreview(src: string, title: string): void {
  imagePreview.value = { src, title }
}

function closeImagePreview(): void {
  imagePreview.value = null
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && imagePreview.value) {
    closeImagePreview()
  }
}

onMounted(() => {
  document.addEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onDocumentKeydown)
})

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

const onAddVoice = (): void => {
  announceBackendPending(`add voice for item ${item.value?.id ?? 'unknown item'}`)
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

const onReleaseVoice = (voice: Voice): void => {
  announceBackendPending(`release ${voice.language.toUpperCase()} voice ${voice.id} for item ${item.value?.id ?? 'unknown item'}`)
}

const onReleaseAllItemVoices = (): void => {
  announceBackendPending(`release voices for item ${item.value?.id ?? 'unknown item'}`)
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
.item-detail-page {
  color: #111a3d;
}
.item-detail-page :deep(.breadcrumb) {
  font-size: 0.82rem;
  font-weight: 600;
}
.id-panel {
  background: #fff;
  border: 1px solid #e6eaf4;
  border-radius: 8px;
  box-shadow: 0 4px 18px rgba(25, 38, 77, 0.04);
  margin-bottom: 1rem;
  padding: 1.25rem 1.5rem;
}
.id-summary-panel {
  display: grid;
  grid-template-columns: minmax(16rem, 0.7fr) minmax(30rem, 1.3fr);
  gap: 1.5rem;
}
.id-summary-copy {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  min-height: 100%;
  padding-right: 1.25rem;
}
.id-summary-details {
  border-left: 1px solid #e2e7f1;
  padding-left: 1.5rem;
  display: grid;
  gap: 1.25rem;
  min-width: 0;
}
.id-field-label,
.id-section-kicker,
.id-meta-label {
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
.id-edit-btn,
.id-action-square {
  border: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
  padding: 0;
}
.id-edit-btn {
  background: transparent;
  color: #2563eb;
}
.id-edit-btn {
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
  width: min(100%, 12rem);
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
}
.id-summary-actions,
.id-section-header,
.id-toolbar {
  display: flex;
  align-items: center;
}
.id-summary-actions,
.id-section-header {
  justify-content: space-between;
  gap: 1rem;
}
.id-summary-actions-only {
  justify-content: flex-end;
}
.id-release-group,
.id-toolbar {
  gap: 0.55rem;
}
.id-square-btn {
  width: 2.2rem;
  padding-left: 0;
  padding-right: 0;
}
.id-section-title {
  color: #111a3d;
  font-size: 1.05rem;
  font-weight: 700;
  margin: 0;
}
.id-section-title span {
  margin-left: 0.35rem;
}
.id-image-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1rem;
  margin-top: 1rem;
}
.id-image-card {
  display: grid;
  grid-template-columns: minmax(10rem, 43%) minmax(0, 1fr);
  gap: 1.25rem;
  min-height: 12.8rem;
  border: 1px solid #dfe5ef;
  border-radius: 6px;
  padding: 0.75rem;
}
.id-image-card-empty {
  max-width: 34rem;
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
.file-empty {
  margin-top: 1rem;
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
.btn-icon-release {
  background: #2f8f68;
}
.btn-icon-release:hover {
  background: #267657;
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
.id-summary-voices-panel {
  min-width: 0;
}
.id-summary-voice-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(7.5rem, 1fr));
  gap: 0.75rem;
  margin-top: 0.8rem;
}
.id-summary-voice-card {
  position: relative;
  min-width: 0;
  min-height: 13rem;
  border: 1.5px solid #b8d4f0;
  border-radius: 8px;
  padding: 2.85rem 0.75rem 0.75rem;
  text-align: center;
  background: #f8fbff;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: space-between;
}
.id-summary-voice-actions {
  position: absolute;
  top: 0.45rem;
  right: 0.45rem;
  display: flex;
  gap: 0.3rem;
}
.id-summary-voice-action {
  width: 1.65rem;
  height: 1.65rem;
  font-size: 0.82rem;
}
.id-summary-voice-flag {
  width: 3.5rem;
  height: 3.5rem;
  border: 2px solid #d1e3f6;
  border-radius: 50%;
  background: #e8f0fe;
  display: flex;
  align-items: center;
  justify-content: center;
}
.id-summary-voice-flag .flag-icon {
  font-size: 1.6rem;
}
.id-summary-voice-body {
  min-width: 0;
  width: 100%;
}
.id-summary-voice-name {
  color: #1e293b;
  font-size: 0.85rem;
  font-weight: 700;
  line-height: 1.2;
  margin: 0;
  overflow-wrap: anywhere;
}
.id-summary-voice-type {
  color: #6b7280;
  font-size: 0.75rem;
  line-height: 1.25;
  margin: 0.25rem 0 0;
}
.btn-play {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  border: 1px solid #4f46e5;
  border-radius: 4px;
  background: #fff;
  color: #4f46e5;
  cursor: pointer;
  font-size: 0.78rem;
  min-height: 1.7rem;
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
.id-voices-table-wrap {
  margin-top: 0.8rem;
  overflow-x: visible;
}
.id-voices-table {
  width: 100%;
  min-width: 0;
  table-layout: fixed;
}
.id-voices-table th {
  border-top: 0;
  color: #111a3d;
  font-size: 0.76rem;
  font-weight: 700;
  white-space: nowrap;
}
.id-voices-table th,
.id-voices-table td {
  overflow: hidden;
  text-overflow: ellipsis;
}
.id-voices-table td {
  color: #111a3d;
  font-size: 0.82rem;
  vertical-align: middle;
  white-space: nowrap;
}
.id-voices-table th:nth-child(1),
.id-voices-table td:nth-child(1) {
  width: 16%;
}
.id-voices-table th:nth-child(2),
.id-voices-table td:nth-child(2) {
  width: 8%;
}
.id-voices-table th:nth-child(3),
.id-voices-table td:nth-child(3) {
  width: 20%;
}
.id-voices-table th:nth-child(4),
.id-voices-table td:nth-child(4),
.id-voices-table th:nth-child(6),
.id-voices-table td:nth-child(6) {
  width: 8%;
  text-align: center;
}
.id-voices-table th:nth-child(5),
.id-voices-table td:nth-child(5) {
  width: 23%;
}
.id-voices-table th:nth-child(7),
.id-voices-table td:nth-child(7) {
  width: 17%;
}
.id-voice-language,
.item-voice-actions,
.id-audio-preview {
  display: flex;
  align-items: center;
}
.id-voice-language {
  gap: 0.55rem;
  font-weight: 600;
  min-width: 0;
}
.id-voice-language span:not(.id-count-pill) {
  overflow: hidden;
  text-overflow: ellipsis;
}
.id-count-pill {
  min-width: 1.25rem;
  border-radius: 4px;
  background: #edf1f7;
  color: #4b587d;
  font-size: 0.72rem;
  line-height: 1.25rem;
  text-align: center;
}
.id-audio-preview {
  gap: 0.35rem;
  color: #4b587d;
  justify-content: center;
}
.id-audio-play {
  width: 1.6rem;
  height: 1.6rem;
  border: 0;
  border-radius: 50%;
  background: #eef1f6;
  color: #111827;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}
.id-audio-play-active {
  background: #ef4444;
  color: #fff;
}
.id-audio-play:disabled {
  color: #94a3b8;
  cursor: not-allowed;
}
.item-voice-actions {
  justify-content: flex-end;
  gap: 0.45rem;
}
.id-action-square {
  width: 1.7rem;
  height: 1.7rem;
  border-radius: 4px;
  font-size: 0.95rem;
}
.id-action-release {
  background: #e9f8f1;
  color: #2f8f68;
}
.id-action-edit {
  background: #eef4ff;
  color: #2563eb;
}
.id-action-delete {
  background: #fff0f1;
  color: #ef4444;
}
@media (max-width: 1399px) {
  .id-image-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 991px) {
  .id-summary-panel {
    grid-template-columns: 1fr;
  }
  .id-summary-details {
    border-left: 0;
    border-top: 1px solid #e2e7f1;
    padding-left: 0;
    padding-top: 1.25rem;
  }
  .id-image-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 575px) {
  .id-panel {
    padding: 1rem;
  }
  .id-section-header,
  .id-summary-actions {
    align-items: flex-start;
    flex-direction: column;
  }
  .id-toolbar,
  .id-release-group {
    flex-wrap: wrap;
  }
  .id-image-card {
    grid-template-columns: 1fr;
  }
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
