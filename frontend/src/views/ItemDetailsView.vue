<template>
  <div v-if="category && item" class="item-detail-page">
    <ViewBreadcrumb :items="breadcrumbItems" />

    <section class="id-panel id-summary-panel">
      <div class="id-summary-header">
        <div class="id-summary-identity">
          <div class="id-field-label">{{ itemIdLabel(contentLanguage) }}</div>
          <div class="id-value-row">
            <h3 class="id-item-id">{{ item.id }}</h3>
            <button type="button" class="id-edit-btn" title="Edit item id" aria-label="Edit item id" @click="onEditItemId">
              <i class="mdi mdi-pencil"></i>
            </button>
          </div>
        </div>

        <div class="id-summary-actions">
          <div class="id-summary-buttons">
            <button
              v-for="action in itemActions"
              :key="action.label"
              type="button"
              class="btn btn-sm"
              :class="action.danger ? 'btn-outline-danger' : `id-summary-action-${action.tone}`"
              :disabled="action.disabled"
              @click="action.onClick"
            >
              <i class="mdi mr-1" :class="action.icon" aria-hidden="true"></i>
              {{ action.label }}
            </button>
          </div>
          <MobileActionsMenu class="id-summary-menu" label="Item actions" :actions="itemActions" />
        </div>
      </div>

      <div class="id-summary-copy">
        <div class="id-summary-main-info">
          <div>
            <div class="id-field-label">{{ currentImageLabel(contentLanguage) }}</div>
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
            <div class="id-field-label">{{ descriptionLabel(contentLanguage) }}</div>
            <p class="id-description">{{ categoryDescriptionText }}</p>
          </div>
        </div>
      </div>

      <div class="id-summary-details">
        <div class="id-summary-voices-panel">
          <h4 class="id-section-kicker">{{ itemVoicesLabel(contentLanguage) }}</h4>
          <VoiceCardsGrid
            :voices="itemVoiceCards"
            :playing-voice-id="playingVoiceId"
            :has-audio="hasAudio"
            :toggle-play="togglePlay"
            :upload-voice="uploadVoiceCard"
            :delete-voice="deleteVoiceCard"
            :rename-voice="renameVoiceHandler"
            :extra-actions="voiceCardActions"
          />
        </div>
      </div>
    </section>

    <input ref="addFileInputRef" type="file" accept="image/*" class="d-none" aria-hidden="true" tabindex="-1" @change="onAddFilePicked" />
    <input ref="replaceFileInputRef" type="file" accept="image/*" class="d-none" aria-hidden="true" tabindex="-1" @change="onReplaceFilePicked" />

    <section class="id-panel">
      <div class="id-section-header">
        <h4 class="id-section-title">
          {{ imagesLabel(contentLanguage) }}
          <span>({{ itemImages.length }})</span>
        </h4>
        <div class="id-summary-actions">
          <div class="id-summary-buttons">
            <button
              v-for="action in imageActions"
              :key="action.label"
              type="button"
              class="btn btn-sm"
              :class="`id-summary-action-${action.tone}`"
              :disabled="action.disabled"
              @click="action.onClick"
            >
              <i class="mdi mr-1" :class="action.icon" aria-hidden="true"></i>
              {{ action.label }}
            </button>
          </div>
          <MobileActionsMenu class="id-summary-menu" label="Image actions" :actions="imageActions" />
        </div>
      </div>

      <div v-if="hasFiles" class="id-image-grid">
        <div
          v-for="card in imageCards"
          :key="card.index"
          class="id-image-card"
          :class="{ 'id-image-card-main': card.isMain }"
        >
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
          </div>
          <div class="id-image-meta">
            <div class="id-meta-label">{{ generatorLabel(contentLanguage) }}</div>
            <strong>{{ card.generator }}</strong>
            <div class="id-meta-label id-meta-spaced">{{ promptLabel(contentLanguage) }}</div>
            <p>{{ card.prompt }}</p>
            <span class="id-mobile-image-file">{{ fileNameFromUri(rawItemImages[card.index]) }}</span>
            <button
              type="button"
              class="id-release-btn"
              :class="{ 'id-release-btn-active': card.isMain }"
              :disabled="card.isMain || isSettingMainImage"
              @click="onSelectImage(card.index)"
            >
              <i class="mdi" :class="card.isMain ? 'mdi-check' : 'mdi-image'"></i>
              {{ card.isMain ? selectedLabel(contentLanguage) : selectImageLabel(contentLanguage) }}
            </button>
          </div>
          <div class="file-actions" @click.stop>
            <button type="button" class="btn-icon btn-icon-edit" title="Replace" aria-label="Replace image" @click.stop="onReplaceFile(card.index)">
              <i class="mdi mdi-pencil"></i>
            </button>
            <button type="button" class="btn-icon btn-icon-delete" title="Delete" aria-label="Delete image" @click.stop="onDeleteFile(card.index)">
              <i class="mdi mdi-delete"></i>
            </button>
          </div>
        </div>
        <div
          class="id-image-card id-add-image-card"
          :class="{ 'id-add-image-card-dragging': isImageDropActive, 'id-add-image-card-uploading': isAddingImage }"
          role="button"
          tabindex="0"
          :aria-label="addImageLabel(contentLanguage)"
          @click="onAddFile"
          @keydown.enter.prevent="onAddFile"
          @keydown.space.prevent="onAddFile"
          @dragenter.prevent="onImageDragEnter"
          @dragover.prevent="onImageDragOver"
          @dragleave.prevent="onImageDragLeave"
          @drop.prevent="onImageDrop"
        >
          <div class="id-add-image-card-content">
            <i class="mdi" :class="isAddingImage ? 'mdi-loading mdi-spin' : 'mdi-plus-circle-outline'"></i>
            <strong>{{ addImageLabel(contentLanguage) }}</strong>
            <span>{{ isAddingImage ? uploadingImageLabel(contentLanguage) : dropImageHereLabel(contentLanguage) }}</span>
          </div>
        </div>
      </div>
      <div v-else class="file-empty">
        <div
          class="id-image-card id-add-image-card"
          :class="{ 'id-add-image-card-dragging': isImageDropActive, 'id-add-image-card-uploading': isAddingImage }"
          role="button"
          tabindex="0"
          :aria-label="addImageLabel(contentLanguage)"
          @click="onAddFile"
          @keydown.enter.prevent="onAddFile"
          @keydown.space.prevent="onAddFile"
          @dragenter.prevent="onImageDragEnter"
          @dragover.prevent="onImageDragOver"
          @dragleave.prevent="onImageDragLeave"
          @drop.prevent="onImageDrop"
        >
          <div class="id-add-image-card-content">
            <i class="mdi" :class="isAddingImage ? 'mdi-loading mdi-spin' : 'mdi-plus-circle-outline'"></i>
            <strong>{{ addImageLabel(contentLanguage) }}</strong>
            <span>{{ isAddingImage ? uploadingImageLabel(contentLanguage) : dropImageHereLabel(contentLanguage) }}</span>
          </div>
        </div>
      </div>
    </section>

    <section class="id-panel">
      <div class="id-section-header">
        <h4 class="id-section-title">
          {{ voicesLabel(contentLanguage) }}
          <span>({{ item.voices.length }})</span>
        </h4>
        <div class="id-summary-actions">
          <div class="id-summary-buttons">
            <button
              v-for="action in voiceActions"
              :key="action.label"
              type="button"
              class="btn btn-sm"
              :class="`id-summary-action-${action.tone}`"
              :disabled="action.disabled"
              @click="action.onClick"
            >
              <i class="mdi mr-1" :class="action.icon" aria-hidden="true"></i>
              {{ action.label }}
            </button>
          </div>
          <MobileActionsMenu class="id-summary-menu" label="Voice actions" :actions="voiceActions" />
        </div>
      </div>
      <input ref="voiceFileInputRef" type="file" accept="audio/*" class="d-none" aria-hidden="true" tabindex="-1" @change="onVoiceFilePicked" />
      <div class="table-responsive id-voices-table-wrap">
        <table class="table id-voices-table mb-0">
          <thead>
            <tr>
              <th>{{ languageColumnLabel(contentLanguage) }}</th>
              <th>{{ generatorLabel(contentLanguage) }}</th>
              <th>{{ normalFileColumnLabel(contentLanguage) }}</th>
              <th>{{ optimizedFileColumnLabel(contentLanguage) }}</th>
              <th class="text-right">{{ actionsColumnLabel(contentLanguage) }}</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="group in groupedVoices" :key="group.language">
              <tr
                v-for="(voice, index) in group.voices"
                v-show="index === 0 || isLanguageExpanded(group.language)"
                :key="voice.id"
              >
                <td>
                  <div
                    v-if="index === 0"
                    class="id-voice-language"
                    :class="{ 'id-voice-language-clickable': group.voices.length > 1 }"
                    :title="langLabel(group.language)"
                    :role="group.voices.length > 1 ? 'button' : undefined"
                    :tabindex="group.voices.length > 1 ? 0 : undefined"
                    :aria-expanded="group.voices.length > 1 ? isLanguageExpanded(group.language) : undefined"
                    :aria-label="langLabel(group.language)"
                    @click="group.voices.length > 1 && toggleLanguageGroup(group.language)"
                    @keydown.enter.prevent="group.voices.length > 1 && toggleLanguageGroup(group.language)"
                    @keydown.space.prevent="group.voices.length > 1 && toggleLanguageGroup(group.language)"
                  >
                    <i
                      class="mdi id-voice-chevron"
                      :class="isLanguageExpanded(group.language) ? 'mdi-chevron-down' : 'mdi-chevron-right'"
                    ></i>
                    <i :class="'flag-icon flag-icon-' + langFlag(group.language)"></i>
                    <span class="id-count-pill">{{ group.voices.length }}</span>
                  </div>
                </td>
                <td><VoiceParametersTooltip :provider="voice.provider" :parameters="voice.parameters" /></td>
                <td>
                  <div class="id-audio-preview">
                    <button class="id-audio-play" :class="{ 'id-audio-play-active': playingVoiceId === voice.id }" :disabled="!hasAudio(voice)" :aria-label="!hasAudio(voice) ? voiceFileMissingLabel(contentLanguage) : (playingVoiceId === voice.id ? stopLabel(contentLanguage) : playLabel(contentLanguage))" :title="!hasAudio(voice) ? voiceFileMissingLabel(contentLanguage) : (playingVoiceId === voice.id ? stopLabel(contentLanguage) : playLabel(contentLanguage))" @click="togglePlay(voice)">
                      <i :class="playingVoiceId === voice.id ? 'mdi mdi-stop' : 'mdi mdi-play'"></i>
                    </button>
                    <span :title="voiceFileName(voice)">{{ voiceFileName(voice) }}</span>
                  </div>
                </td>
                <td>
                  <div class="id-audio-preview">
                    <button class="id-audio-play" :class="{ 'id-audio-play-active': playingVoiceId === voice.id }" :disabled="!hasAudio(voice)" :aria-label="!hasAudio(voice) ? voiceFileMissingLabel(contentLanguage) : (playingVoiceId === voice.id ? stopLabel(contentLanguage) : playLabel(contentLanguage))" :title="!hasAudio(voice) ? voiceFileMissingLabel(contentLanguage) : (playingVoiceId === voice.id ? stopLabel(contentLanguage) : playLabel(contentLanguage))" @click="togglePlay(voice)">
                      <i :class="playingVoiceId === voice.id ? 'mdi mdi-stop' : 'mdi mdi-play'"></i>
                    </button>
                    <span :title="optimizedVoiceFileName(voice)">{{ optimizedVoiceFileName(voice) }}</span>
                  </div>
                </td>
                <td>
                  <div class="item-voice-actions">
                    <button class="id-action-square id-action-regenerate" type="button" :disabled="isRegeneratingVoices" title="Regenerate voice" aria-label="Regenerate voice" @click="regenerateVoice(voice)">
                      <i class="mdi" :class="regeneratingVoiceId === voice.id ? 'mdi-loading mdi-spin' : 'mdi-auto-fix'"></i>
                    </button>
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
    <div
      v-if="showRegenerateAllVoices"
      class="cd-modal-overlay"
      @click.self="closeRegenerateAllVoicesModal"
      @keydown.esc.stop="closeRegenerateAllVoicesModal"
    >
      <div class="cd-modal-box" role="dialog" aria-modal="true" aria-labelledby="regenerate-all-title" aria-describedby="regenerate-all-description" @keydown.tab="trapRegenerateConfirmationFocus">
        <div class="cd-modal-header">
          <h5 id="regenerate-all-title" class="cd-modal-title">{{ regenerateAllVoicesLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" aria-label="Close" @click="closeRegenerateAllVoicesModal">&times;</button>
        </div>
        <div class="cd-modal-body">
          <p id="regenerate-all-description">{{ regenerateAllVoicesConfirmation.beforeItemId }}<strong>{{ item?.id }}</strong>{{ regenerateAllVoicesConfirmation.afterItemId }}</p>
        </div>
        <div class="cd-modal-footer">
          <button ref="regenerateAllNoButton" type="button" class="btn btn-secondary" @click="closeRegenerateAllVoicesModal">{{ noLabel(contentLanguage) }}</button>
          <button type="button" class="btn id-summary-action-regenerate" :disabled="isRegeneratingVoices" @click="regenerateAllVoices">{{ yesLabel(contentLanguage) }}</button>
        </div>
      </div>
    </div>

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
          <h5 class="cd-modal-title">{{ changeItemIdLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeItemIdEdit">&times;</button>
        </div>
        <div class="cd-modal-body">
          <div class="form-group mb-0">
            <label class="col-form-label">{{ newItemIdLabel(contentLanguage) }}</label>
            <input
              v-model="itemIdEditValue"
              type="text"
              class="form-control"
              :placeholder="enterItemIdLabel(contentLanguage)"
              @keyup.enter="confirmItemIdEdit"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeItemIdEdit">{{ cancelLabel(contentLanguage) }}</button>
          <button type="button" class="btn btn-primary" @click="confirmItemIdEdit">{{ saveLabel(contentLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <Teleport to="body">
    <div v-if="showDeleteItem" class="cd-modal-overlay" @click.self="closeDeleteItemModal">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ deleteLabel(contentLanguage) }} Item</h5>
          <button type="button" class="cd-modal-close" @click="closeDeleteItemModal">&times;</button>
        </div>
        <div class="cd-modal-body">
          <p class="mb-3">
            This will permanently delete the item <strong>{{ item?.id }}</strong> and all of its images and voices.
          </p>
          <div v-if="usesComplexDeleteItemConfirmation" class="alert alert-warning py-2">
            <small>
              <i class="mdi mdi-alert mr-1"></i>
              This action cannot be undone.
            </small>
          </div>
          <div v-if="usesComplexDeleteItemConfirmation" class="form-group mb-0">
            <label class="col-form-label">
              {{ archiveTypeToConfirmLabel(contentLanguage, item?.id ?? '') }}
            </label>
            <input
              v-model="deleteItemConfirmText"
              type="text"
              class="form-control"
              :placeholder="item?.id"
              @keyup.enter="confirmDeleteItem"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeDeleteItemModal">
            {{ usesComplexDeleteItemConfirmation ? cancelLabel(contentLanguage) : noLabel(contentLanguage) }}
          </button>
          <button type="button" class="btn btn-danger" :disabled="!canDeleteItem || deletingItem" @click="confirmDeleteItem">
            {{ usesComplexDeleteItemConfirmation ? deleteLabel(contentLanguage) : yesLabel(contentLanguage) }}
          </button>
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
import { ref, computed, watch, nextTick, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { AxiosError } from 'axios'
import {
  bumpItemThumbnailVersion,
  itemThumbnailVersions,
  ensureCategoriesLoaded,
  ensureCategoryDetail,
  ensureItemDetail,
  invalidateCategoriesCache,
  invalidateItemDetail,
  sharedCategories,
  updateCachedItemImages,
} from '../api/cache'
import {
  itemsImagesAdd,
  itemsImagesUpdate,
  itemsImagesDelete,
  itemsImagesSetMain,
  itemsDelete,
  itemsUpdate,
  itemsVoicesAdd,
  itemsVoicesUpdate,
  itemsVoicesDelete,
  itemsVoicesRegenerate,
} from '../api/contentClient'
import { LocalizedString, SUPPORTED_LANGUAGES, type Language, type Voice } from '../types/viewModels'
import { useContentLanguage } from '../composables/useContentLanguage'
import { useAudioPlayer } from '../composables/useAudioPlayer'
import ViewBreadcrumb from '../components/ViewBreadcrumb.vue'
import MobileActionsMenu, { type MobileAction } from '../components/MobileActionsMenu.vue'
import VoiceCardsGrid, { type VoiceExtraAction } from '../components/VoiceCardsGrid.vue'
import VoiceParametersTooltip from '../components/VoiceParametersTooltip.vue'
import { resizeImageToMaxDimension } from '../utils/imageResize'
import { imageIdFromUri } from '../utils/imageId'
import { cacheBustedUri } from '../utils/cacheBustedUri'
import { langFlag, langLabel } from '../utils/voiceLabels'
import cameraPlaceholderUrl from '../assets/images/samples/camera-placeholder.svg'
import {
  addImageLabel,
  addVoiceLabel,
  actionsColumnLabel,
  breadcrumbCategoriesLabel,
  archiveTypeToConfirmLabel,
  cancelLabel,
  changeItemIdLabel,
  currentImageLabel,
  descriptionLabel,
  deleteLabel,
  dropImageFileRequiredLabel,
  dropImageHereLabel,
  enterItemIdLabel,
  imagesLabel,
  generatorLabel,
  imageVariantLabel,
  itemIdLabel,
  itemVoicesLabel,
  languageColumnLabel,
  localizedCategoryLabel,
  localizedImageLabel,
  mainItemImageLabel,
  newItemIdLabel,
  noLabel,
  normalFileColumnLabel,
  optimizedFileColumnLabel,
  playLabel,
  promptLabel,
  regenerateAllVoicesLabel,
  regenerateAllVoicesConfirmParts,
  releaseAllImagesLabel,
  releaseAllVoicesLabel,
  releaseItemLabel,
  saveLabel,
  selectedLabel,
  selectImageLabel,
  stopLabel,
  sourceFileLabel,
  uploadingImageLabel,
  voiceFileMissingLabel,
  voicesLabel,
  yesLabel,
} from '../utils/localizedLabels'
import { toPascalCaseWords } from '../utils/pascalCaseWords'

const route = useRoute()
const router = useRouter()

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

const rawItemImages = computed(() => item.value?.images.filter((image) => image.trim().length > 0) ?? [])
const itemImages = computed(() =>
  rawItemImages.value.map((uri) => cacheBustedUri(uri, imageVersions.value[imageIdFromUri(uri)] ?? 0)),
)
const hasFiles = computed(() => itemImages.value.length > 0)

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

const breadcrumbItems = computed(() => [
  { label: breadcrumbRoot.value, to: { name: 'dashboard' } },
  { label: breadcrumbCategory.value, to: { name: 'category', params: { categoryId: category.value?.id ?? '' } } },
  { label: breadcrumbItem.value },
])

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

// The main image is a copy of one of the variants below; this is the variant it was
// copied from, i.e. the card to mark as selected and keep out of re-selection.
const mainImageId = computed(() => item.value?.mainImageId ?? '')

const imageCards = computed(() =>
  itemImages.value.map((src, index) => {
    const rawUri = rawItemImages.value[index]
    const isMain = mainImageId.value !== '' && imageIdFromUri(rawUri) === mainImageId.value
    return {
      src,
      index,
      isMain,
      title: `${breadcrumbItem.value} image ${index + 1}`,
      generator: isMain
        ? mainItemImageLabel(contentLanguage.value)
        : imageVariantLabel(contentLanguage.value, index + 1),
      prompt: `${sourceFileLabel(contentLanguage.value)}: ${fileNameFromUri(rawUri)}`,
    }
  }),
)

// The main image is a separate file from the variants gallery above — never part of
// `item.images`, only exposed via `item.thumbnailUri` — and holds a copy of the variant
// named by `item.mainImageId`.
const mainImageUri = computed(() => item.value?.thumbnailUri?.trim() || '')
const mainImageVersion = computed(() => {
  if (!category.value || !item.value) return 0
  return itemThumbnailVersions.value.get(`${category.value.id}:${item.value.id}`) ?? 0
})
const currentImageCard = computed(() => {
  const rawUri = mainImageUri.value
  if (!rawUri) return null
  return {
    src: cacheBustedUri(rawUri, mainImageVersion.value),
    title: `${breadcrumbItem.value} main image`,
    generator: mainItemImageLabel(contentLanguage.value),
    prompt: `${sourceFileLabel(contentLanguage.value)}: ${fileNameFromUri(rawUri)}`,
  }
})

const groupedVoices = computed(() =>
  SUPPORTED_LANGUAGES
    .map((language) => ({
      language,
      voices: item.value?.voices.filter((voice) => voice.language === language) ?? [],
    }))
    .filter((group) => group.voices.length > 0),
)

const expandedVoiceLanguages = ref<Set<Language>>(new Set())

function isLanguageExpanded(language: Language): boolean {
  return expandedVoiceLanguages.value.has(language)
}

function toggleLanguageGroup(language: Language): void {
  const next = new Set(expandedVoiceLanguages.value)
  if (next.has(language)) {
    next.delete(language)
  } else {
    next.add(language)
  }
  expandedVoiceLanguages.value = next
}

const itemVoiceCards = computed(() =>
  SUPPORTED_LANGUAGES.flatMap((language) =>
    item.value?.voices.filter((voice) => voice.language === language) ?? [],
  ),
)

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

const confirmItemIdEdit = async (): Promise<void> => {
  const cat = category.value
  const currentItem = item.value
  const newItemId = itemIdEditValue.value.trim()
  showItemIdEdit.value = false
  if (!cat || !currentItem || !newItemId || newItemId === currentItem.id) return
  try {
    // item_name is required by the endpoint; resending the current names leaves them unchanged.
    const result = await itemsUpdate(cat.id, currentItem.id, {
      new_item_id: newItemId,
      item_name: currentItem.localizedName ?? new LocalizedString({}),
    })
    // The item's id is part of the cached category list and of the route, so reload the
    // list and then move to the renamed item's URL.
    invalidateCategoriesCache()
    await ensureCategoriesLoaded()
    await router.replace({ name: 'item-detail', params: { categoryId: cat.id, itemId: result.id } })
    showToast(`Item id changed to ${result.id}.`, 'success')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}

const addFileInputRef = ref<HTMLInputElement | null>(null)
const isImageDropActive = ref(false)
const isAddingImage = ref(false)

const onAddFile = (): void => {
  if (isAddingImage.value) return
  addFileInputRef.value?.click()
}

const uploadNewImage = async (file: File): Promise<void> => {
  if (isAddingImage.value) return
  const cat = category.value
  const currentItem = item.value
  if (!cat || !currentItem) return

  isAddingImage.value = true
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
    isAddingImage.value = false
  }
}

const onAddFilePicked = async (e: Event): Promise<void> => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) await uploadNewImage(file)
}

const onImageDragEnter = (): void => {
  if (!isAddingImage.value) isImageDropActive.value = true
}

const onImageDragOver = (): void => {
  if (!isAddingImage.value) isImageDropActive.value = true
}

const onImageDragLeave = (event: DragEvent): void => {
  const dropTarget = event.currentTarget
  if (!(dropTarget instanceof HTMLElement) || !dropTarget.contains(event.relatedTarget as Node | null)) {
    isImageDropActive.value = false
  }
}

const onImageDrop = async (event: DragEvent): Promise<void> => {
  isImageDropActive.value = false
  const file = Array.from(event.dataTransfer?.files ?? []).find((entry) => entry.type.startsWith('image/'))
  if (!file) {
    showToast(dropImageFileRequiredLabel(contentLanguage.value), 'error')
    return
  }
  await uploadNewImage(file)
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

const showDeleteItem = ref(false)
const deleteItemConfirmText = ref('')
const deletingItem = ref(false)
const usesComplexDeleteItemConfirmation = import.meta.env.VITE_DELETE_ITEM_CONFIRMATION_MODE !== 'simple'

const canDeleteItem = computed(() =>
  !usesComplexDeleteItemConfirmation || deleteItemConfirmText.value.trim() === item.value?.id,
)

function openDeleteItemModal(): void {
  deleteItemConfirmText.value = ''
  showDeleteItem.value = true
}

function closeDeleteItemModal(): void {
  if (deletingItem.value) return
  showDeleteItem.value = false
  deleteItemConfirmText.value = ''
}

async function confirmDeleteItem(): Promise<void> {
  const cat = category.value
  const currentItem = item.value
  if (!cat || !currentItem || !canDeleteItem.value || deletingItem.value) return

  deletingItem.value = true
  try {
    await itemsDelete(cat.id, currentItem.id)
    if (playingVoiceId.value) stopAudio()
    invalidateItemDetail(cat.id, currentItem.id)
    const categoryIndex = sharedCategories.value.findIndex((entry) => entry.id === cat.id)
    if (categoryIndex !== -1) {
      const categories = [...sharedCategories.value]
      const cachedCategory = categories[categoryIndex]
      categories[categoryIndex] = {
        ...cachedCategory,
        items: cachedCategory.items.filter((entry) => entry.id !== currentItem.id),
        items_count: Math.max(0, (cachedCategory.items_count ?? cachedCategory.items.length) - 1),
      }
      sharedCategories.value = categories
    }
    await router.replace({ name: 'category', params: { categoryId: cat.id } })
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  } finally {
    deletingItem.value = false
  }
}

const isSettingMainImage = ref(false)

const onSelectImage = async (index: number): Promise<void> => {
  const cat = category.value
  const currentItem = item.value
  const rawUri = rawItemImages.value[index]
  if (!cat || !currentItem || !rawUri || isSettingMainImage.value) return
  const imageId = imageIdFromUri(rawUri)
  if (imageId === mainImageId.value) return // Already the main image.

  isSettingMainImage.value = true
  try {
    // Only the main image file is rewritten; the variants are left untouched.
    // The shared version refreshes this preview and the item grid across navigation.
    const updatedImages = await itemsImagesSetMain(cat.id, currentItem.id, imageId)
    updateCachedItemImages(cat.id, currentItem.id, updatedImages)
    bumpItemThumbnailVersion(cat.id, currentItem.id)
    showToast('Main image updated.', 'success')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  } finally {
    isSettingMainImage.value = false
  }
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

async function uploadVoiceCard(voice: Voice, file: File, wasAdd: boolean): Promise<void> {
  const cat = category.value
  const currentItem = item.value
  if (!cat || !currentItem) return
  try {
    const formData = new FormData()
    if (wasAdd) formData.append('language', voice.language)
    formData.append('audio', file)
    const result = wasAdd
      ? await itemsVoicesAdd(cat.id, currentItem.id, formData)
      : await itemsVoicesUpdate(cat.id, currentItem.id, voice.id, formData)
    await refreshItem()
    voiceVersions.value = { ...voiceVersions.value, [voice.id]: (voiceVersions.value[voice.id] ?? 0) + 1 }
    showToast(`Voice for ${voice.name} (${voice.language.toUpperCase()}) ${pastTense(result.action)}.`, 'success')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}

async function deleteVoiceCard(voice: Voice): Promise<void> {
  const cat = category.value
  const currentItem = item.value
  if (!cat || !currentItem) return
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

function localizedItemNameForPatch(update: { language: Language; value: string }): LocalizedString {
  const currentItem = item.value
  const values = Object.fromEntries(
    SUPPORTED_LANGUAGES.map((language) => [
      language,
      update.language === language
        ? update.value
        : currentItem?.voices.find((v) => v.language === language)?.name ?? currentItem?.name ?? '',
    ]),
  )
  return new LocalizedString(values)
}

const regeneratingVoiceId = ref<string | null>(null)
const regeneratingAllVoices = ref(false)
const isRegeneratingVoices = computed(() => regeneratingAllVoices.value || regeneratingVoiceId.value !== null)
const regenerateAllVoicesConfirmation = computed(() => regenerateAllVoicesConfirmParts(contentLanguage.value))
const showRegenerateAllVoices = ref(false)
const regenerateAllNoButton = ref<HTMLButtonElement | null>(null)
let regenerateAllTrigger: HTMLElement | null = null

async function openRegenerateAllVoicesModal(): Promise<void> {
  if (!item.value?.voices.length || isRegeneratingVoices.value) return
  regenerateAllTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
  showRegenerateAllVoices.value = true
  await nextTick()
  regenerateAllNoButton.value?.focus()
}

function closeRegenerateAllVoicesModal(): void {
  showRegenerateAllVoices.value = false
  regenerateAllTrigger?.focus()
}

function trapRegenerateConfirmationFocus(event: KeyboardEvent): void {
  const dialog = event.currentTarget as HTMLElement
  const buttons = dialog.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')
  const first = buttons[0]
  const last = buttons[buttons.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}

watch(() => route.fullPath, () => { showRegenerateAllVoices.value = false })

async function regenerateAllVoices(): Promise<void> {
  const cat = category.value
  const currentItem = item.value
  if (!showRegenerateAllVoices.value || !cat || !currentItem?.voices.length || isRegeneratingVoices.value) return

  const voices = [...currentItem.voices]
  closeRegenerateAllVoicesModal()
  regeneratingAllVoices.value = true
  stopAudio()
  const failures: string[] = []
  try {
    for (const voice of voices) {
      regeneratingVoiceId.value = voice.id
      try {
        await itemsVoicesRegenerate(cat.id, currentItem.id, voice.id)
        voiceVersions.value = { ...voiceVersions.value, [voice.id]: (voiceVersions.value[voice.id] ?? 0) + 1 }
      } catch (err) {
        failures.push(`${voice.language.toUpperCase()} (${voice.id}): ${extractErrorMessage(err)}`)
      }
    }
    invalidateItemDetail(cat.id, currentItem.id)
    await ensureItemDetail(cat.id, currentItem.id)
    showToast(
      failures.length
        ? `Regenerated ${voices.length - failures.length} of ${voices.length} voices. Failed: ${failures.join('; ')}`
        : `All ${voices.length} voices regenerated.`,
      failures.length ? 'error' : 'success',
    )
  } catch (err) {
    showToast(`Voice regeneration finished, but refreshing the item failed. ${extractErrorMessage(err)}`, 'error')
  } finally {
    regeneratingVoiceId.value = null
    regeneratingAllVoices.value = false
  }
}

async function regenerateVoice(voice: Voice): Promise<void> {
  const cat = category.value
  const currentItem = item.value
  if (!cat || !currentItem || isRegeneratingVoices.value) return
  if (playingVoiceId.value === voice.id) stopAudio()

  regeneratingVoiceId.value = voice.id
  try {
    await itemsVoicesRegenerate(cat.id, currentItem.id, voice.id)
    await refreshItem()
    voiceVersions.value = { ...voiceVersions.value, [voice.id]: (voiceVersions.value[voice.id] ?? 0) + 1 }
    showToast(`Voice for ${voice.name} (${voice.language.toUpperCase()}) regenerated.`, 'success')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  } finally {
    regeneratingVoiceId.value = null
  }
}

async function renameVoiceHandler(voice: Voice, name: string): Promise<void> {
  const cat = category.value
  const currentItem = item.value
  if (!cat || !currentItem) return
  try {
    const itemName = localizedItemNameForPatch({ language: voice.language, value: name })
    await itemsUpdate(cat.id, currentItem.id, { item_name: itemName })
    await refreshItem()
    showToast(`Voice text for ${voice.language.toUpperCase()} updated.`, 'success')
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}

const voiceCardActions: VoiceExtraAction[] = [
  {
    icon: 'mdi-auto-fix',
    title: 'Regenerate voice',
    onClick: (voice) => { void regenerateVoice(voice) },
    isLoading: (voice) => regeneratingVoiceId.value === voice.id,
    isDisabled: () => isRegeneratingVoices.value,
  },
]

const itemActions = computed<MobileAction[]>(() => [
  {
    label: regenerateAllVoicesLabel(contentLanguage.value),
    icon: regeneratingAllVoices.value ? 'mdi-loading mdi-spin' : 'mdi-auto-fix',
    tone: 'regenerate',
    onClick: openRegenerateAllVoicesModal,
    disabled: isRegeneratingVoices.value || deletingItem.value || !item.value?.voices.length,
  },
  { label: releaseItemLabel(contentLanguage.value), icon: 'mdi-upload', tone: 'success', onClick: onReleaseItem },
  {
    label: deleteLabel(contentLanguage.value),
    icon: 'mdi-delete',
    onClick: openDeleteItemModal,
    danger: true,
    disabled: deletingItem.value || isRegeneratingVoices.value,
  },
])

const imageActions = computed<MobileAction[]>(() => [
  { label: addImageLabel(contentLanguage.value), icon: 'mdi-plus', tone: 'primary', onClick: onAddFile },
  ...(hasFiles.value
    ? [{ label: releaseAllImagesLabel(contentLanguage.value), icon: 'mdi-upload', tone: 'success' as const, onClick: onReleaseAllItemImages }]
    : []),
])

const voiceActions = computed<MobileAction[]>(() => [
  { label: addVoiceLabel(contentLanguage.value), icon: 'mdi-plus', tone: 'primary', onClick: onAddVoice },
  { label: releaseAllVoicesLabel(contentLanguage.value), icon: 'mdi-upload', tone: 'success' as const, onClick: onReleaseAllItemVoices },
])

</script>

<style scoped>
.item-detail-page {
  container-type: inline-size;
  container-name: item-details;
  color: #111a3d;
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
  position: relative;
  display: grid;
  grid-template-columns: minmax(16rem, 0.7fr) minmax(0, 1.3fr);
  gap: 1.5rem;
}
.id-summary-header {
  grid-column: 1 / -1;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}
.id-summary-identity {
  min-width: 0;
}
.id-summary-buttons {
  display: none;
  gap: 0.55rem;
}
.id-summary-buttons .btn {
  white-space: nowrap;
}
.id-summary-actions {
  flex: none;
}
.id-summary-action-primary {
  background: #eef4ff;
  color: #2563eb;
  border: 1px solid #2563eb;
}
.id-summary-action-success {
  background: #e9f8f1;
  color: #24704f;
  border: 1px solid #24704f;
}
.id-summary-action-regenerate {
  background: #e6f4f3;
  color: #0f766e;
  border: 1px solid #0f766e;
}
.id-summary-action-primary:hover,
.id-summary-action-success:hover,
.id-summary-action-regenerate:hover {
  filter: brightness(0.95);
}
@container item-details (min-width: 64rem) {
  .id-summary-buttons { display: flex; }
  .id-summary-menu { display: none; }
}
.id-summary-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  min-height: 100%;
}
.id-summary-main-info {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}
.id-summary-details {
  padding-left: 1.5rem;
  padding-top: 0;
  display: grid;
  align-content: start;
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
  min-width: 0;
  overflow-wrap: anywhere;
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
  flex: none;
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
}
.id-summary-actions,
.id-section-header {
  display: flex;
  align-items: center;
}
.id-summary-actions,
.id-section-header {
  justify-content: space-between;
  gap: 1rem;
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
.id-mobile-image-file {
  display: none;
}
.id-image-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1rem;
  margin-top: 1rem;
}
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
.id-add-image-card {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 12.8rem;
  border: 2px dashed #b8c7e8;
  background: #f8faff;
  color: #2855a6;
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}
.id-add-image-card:hover,
.id-add-image-card:focus {
  border-color: #2563eb;
  background: #eef4ff;
  color: #1d4ed8;
  outline: none;
}
.id-add-image-card-dragging {
  border-color: #2563eb;
  background: #dbeafe;
  color: #1d4ed8;
}
.id-add-image-card-uploading {
  cursor: progress;
  opacity: 0.75;
}
.id-add-image-card-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 15rem;
  padding: 1rem;
  text-align: center;
}
.id-add-image-card-content > i {
  margin-bottom: 0.55rem;
  font-size: 2.25rem;
}
.id-add-image-card-content > strong {
  font-size: 0.95rem;
}
.id-add-image-card-content > span {
  margin-top: 0.35rem;
  color: #64748b;
  font-size: 0.8rem;
  line-height: 1.35;
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
.file-empty .id-add-image-card {
  max-width: 34rem;
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
.id-summary-voices-panel {
  align-self: start;
  min-width: 0;
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
  width: 11%;
}
.id-voices-table th:nth-child(2),
.id-voices-table td:nth-child(2) {
  width: 8%;
}
.id-voices-table td:nth-child(2) {
  /* Let the provider-name tooltip popover escape the cell instead of being clipped. */
  overflow: visible;
}
.id-voices-table th:nth-child(3),
.id-voices-table td:nth-child(3) {
  width: 31%;
}
.id-voices-table th:nth-child(4),
.id-voices-table td:nth-child(4) {
  width: 33%;
}
.id-voices-table th:nth-child(5),
.id-voices-table td:nth-child(5) {
  width: 17%;
}
.id-voice-language,
.item-voice-actions,
.id-audio-preview {
  display: flex;
  align-items: center;
}
.id-voice-language {
  gap: 0.4rem;
  font-weight: 600;
  min-width: 0;
}
.id-voice-language-clickable {
  cursor: pointer;
}
.id-voice-chevron,
.id-voice-language .flag-icon,
.id-count-pill {
  flex-shrink: 0;
}
.id-voice-chevron {
  transition: transform 0.15s ease;
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
  gap: 0.5rem;
  color: #4b587d;
  min-width: 0;
}
.id-audio-preview span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
.id-action-regenerate {
  background: #e6f4f3;
  color: #0f766e;
}
.id-action-square:disabled {
  cursor: not-allowed;
  opacity: 0.55;
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
    border-top: 1px solid #e2e7f1;
    padding-left: 0;
    padding-top: 1.25rem;
  }
  .id-image-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 575px) {
  .id-summary-panel {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    padding: 1rem;
  }
  .id-summary-copy {
    gap: 1rem;
  }
  .id-summary-main-info {
    gap: 1rem;
  }
  .id-summary-image-preview {
    aspect-ratio: 4 / 3;
  }
  .id-summary-details {
    display: block;
    padding: 1rem 0 0;
    border-top: 1px solid #e2e7f1;
  }
  .id-panel {
    padding: 1rem;
  }
  .id-section-header {
    flex-direction: row;
    align-items: center;
    gap: 0.5rem;
  }
  .id-image-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.65rem;
  }
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
  .id-image-grid > .id-add-image-card {
    display: none;
  }
  .id-voices-table-wrap {
    overflow-x: auto;
  }
  .id-voices-table {
    min-width: 42rem;
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
