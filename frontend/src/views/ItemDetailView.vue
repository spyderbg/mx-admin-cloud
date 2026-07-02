<template>
  <div v-if="category && item">
    <!-- Breadcrumb -->
    <nav aria-label="breadcrumb">
      <ol class="breadcrumb bg-transparent p-0 mb-3">
        <li class="breadcrumb-item">
          <router-link :to="{ name: 'dashboard' }">{{ breadcrumbRoot }}</router-link>
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
        <h4 class="card-title">Item Name</h4>
        <div class="form-group mb-0">
          <input
            type="text"
            class="form-control"
            :value="item.name"
            @input="onNameChange"
          />
        </div>
      </div>
    </div>

    <!-- Files -->
    <div class="card mb-4">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-center mb-3">
          <h4 class="card-title mb-0">Files</h4>
          <input
            ref="addFileInputRef"
            type="file"
            accept="image/*"
            class="d-none"
            aria-hidden="true"
            tabindex="-1"
            @change="onAddFilePicked"
          />
          <button v-if="hasFiles" type="button" class="btn btn-primary btn-sm" @click="onAddFile">
            <i class="mdi mdi-plus mr-1"></i>Add File
          </button>
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
                  class="btn btn-sm btn-outline-light"
                  title="Replace"
                  @click="onReplaceFile(index)"
                >
                  <i class="mdi mdi-pencil"></i>
                </button>
                <button
                  class="btn btn-sm btn-outline-light"
                  title="Delete"
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
                class="btn btn-sm btn-outline-light"
                title="Edit"
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
        <h4 class="card-title">Voices</h4>
        <div class="table-responsive">
          <table class="table table-hover mb-0">
            <thead>
              <tr>
                <th>Language</th>
                <th>Word</th>
                <th>Type</th>
                <th style="width: 120px;">Actions</th>
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
                  <div class="d-flex align-items-center" style="gap: 0.4rem;">
                    <button
                      class="btn btn-sm py-0 px-2"
                      :class="playingVoiceId === voice.id ? 'btn-danger' : 'btn-outline-success'"
                      :disabled="!hasAudio(voice)"
                      :title="!hasAudio(voice) ? 'Voice file missing' : (playingVoiceId === voice.id ? 'Stop' : 'Play')"
                      @click="togglePlay(voice)"
                    >
                      <i :class="playingVoiceId === voice.id ? 'mdi mdi-stop' : 'mdi mdi-play'"></i>
                    </button>
                    <button class="btn btn-outline-primary btn-sm py-0 px-2" @click="onUploadVoice(voice.language)">
                      <i class="mdi mdi-upload mr-1"></i>Replace
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
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ensureCategoriesLoaded, ensureCategoryFull, sharedCategories } from '../api/cache'
import type { Language, Voice } from '../types/viewModels'
import { useContentLanguage } from '../composables/useContentLanguage'
import cameraPlaceholderUrl from '../assets/images/samples/camera-placeholder.svg'
import {
  breadcrumbCategoriesLabel,
  localizedCategoryLabel,
  localizedImageLabel,
} from '../utils/localizedLabels'
import { toPascalCaseWords } from '../utils/pascalCaseWords'

const route = useRoute()
const contentLanguage = useContentLanguage()

onMounted(async () => {
  await ensureCategoriesLoaded()
})

watch(() => route.params.categoryId as string, async (id) => {
  if (!id) return
  const cat = sharedCategories.value.find(
    (c) => c.id.toLowerCase() === id.toLowerCase(),
  )
  if (cat) {
    await ensureCategoryFull(cat.name.en)
  }
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
const itemImages = computed(() => item.value?.images.filter((image) => image.trim().length > 0) ?? [])
const hasFiles = computed(() => itemImages.value.length > 0)

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

const onNameChange = (e: Event): void => {
  const val = (e.target as HTMLInputElement).value
  console.log('Name changed to:', val)
}
const addFileInputRef = ref<HTMLInputElement | null>(null)

const onAddFile = (): void => {
  addFileInputRef.value?.click()
}

const onAddFilePicked = (e: Event): void => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) {
    console.log('Add file to item:', item.value?.id, file.name)
  }
  input.value = ''
}
const replaceFileInputRef = ref<HTMLInputElement | null>(null)
const replaceFileIndex = ref<number>(-1)

const onReplaceFile = (index: number): void => {
  replaceFileIndex.value = index
  replaceFileInputRef.value?.click()
}

const onReplaceFilePicked = (e: Event): void => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) {
    console.log('Replace file at index:', replaceFileIndex.value, 'with:', file.name)
  }
  input.value = ''
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

const confirmDelete = (): void => {
  console.log('Delete file at index:', deleteFileIndex.value)
  deleteConfirmVisible.value = false
  deleteFileIndex.value = -1
}
const onUploadVoice = (language: Language): void => {
  console.log('Upload voice for language:', language)
}

const playingVoiceId = ref<string | null>(null)
let activeAudio: HTMLAudioElement | null = null

function hasAudio(voice: Voice): boolean {
  return !!(voice.fileUri && voice.fileUri.trim())
}

function stopAudio(): void {
  if (activeAudio) {
    activeAudio.pause()
    activeAudio.currentTime = 0
    activeAudio = null
  }
  playingVoiceId.value = null
}

function togglePlay(voice: Voice): void {
  if (!hasAudio(voice)) return
  if (playingVoiceId.value === voice.id) {
    stopAudio()
    return
  }
  stopAudio()
  const audio = new Audio(voice.fileUri)
  activeAudio = audio
  playingVoiceId.value = voice.id
  audio.addEventListener('ended', stopAudio, { once: true })
  audio.play()
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
  bottom: 8px;
  right: 8px;
  display: flex;
  gap: 4px;
}
.file-actions .btn {
  width: 30px;
  height: 30px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  background: rgba(0, 0, 0, 0.5);
  border-color: rgba(255, 255, 255, 0.5);
}
.file-actions .btn:hover {
  background: rgba(0, 0, 0, 0.7);
}
.file-empty {
  max-width: 320px;
}
.file-card-empty {
  background: #eff3f8;
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
</style>
