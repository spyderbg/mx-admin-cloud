<template>
  <div v-if="category && image">
    <!-- Breadcrumb -->
    <nav aria-label="breadcrumb">
      <ol class="breadcrumb bg-transparent p-0 mb-3">
        <li class="breadcrumb-item">
          <router-link to="/categories">Categories</router-link>
        </li>
        <li class="breadcrumb-item">
          <router-link :to="`/categories/${category.id}`">{{ category.name }}</router-link>
        </li>
        <li class="breadcrumb-item active" aria-current="page">{{ image.name }}</li>
      </ol>
    </nav>

    <div class="page-header">
      <h3 class="page-title">{{ image.name }}</h3>
    </div>

    <!-- Image name edit -->
    <div class="card mb-4">
      <div class="card-body">
        <h4 class="card-title">Image Name</h4>
        <div class="form-group mb-0">
          <input
            type="text"
            class="form-control"
            :value="image.name"
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
          <button class="btn btn-primary btn-sm" @click="onAddFile">
            <i class="mdi mdi-plus mr-1"></i>Add File
          </button>
        </div>
        <div class="row">
          <div
            v-for="(file, index) in image.files"
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
                  :disabled="image.files.length <= 1"
                  @click="onDeleteFile(index)"
                >
                  <i class="mdi mdi-delete"></i>
                </button>
              </div>
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
                <th>Voice Name</th>
                <th>Type</th>
                <th style="width: 120px;">Actions</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="voice in image.voices" :key="voice.id">
                <td>
                  <i :class="'flag-icon flag-icon-' + langFlag(voice.language)" style="margin-right: 8px;"></i>
                  {{ langLabel(voice.language) }}
                </td>
                <td>
                  <div class="d-flex align-items-center" style="gap: 0.5rem;">
                    <img
                      :src="voice.iconUrl"
                      alt=""
                      class="rounded-circle"
                      style="width: 28px; height: 28px; object-fit: cover;"
                    />
                    {{ voice.name }}
                  </div>
                </td>
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
    <h4 class="text-muted">Image not found</h4>
    <router-link to="/categories" class="btn btn-primary mt-3">Back to Categories</router-link>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getCategory } from '../api/categories'
import type { Category, Language, Voice } from '../types'

const route = useRoute()

const categoryData = ref<Category | null>(null)

const loadCategory = async (id: string) => {
  categoryData.value = await getCategory(id)
}

onMounted(() => {
  const id = route.params.categoryId as string
  if (id) loadCategory(id)
})

watch(() => route.params.categoryId as string, (id) => {
  if (id) loadCategory(id)
})

const category = computed(() => categoryData.value)

const image = computed(() => {
  const imgId = route.params.imageId as string
  return category.value?.images.find(i => i.id === imgId) ?? null
})

const langFlagMap: Record<Language, string> = { en: 'us', es: 'es', fr: 'fr', bg: 'bg' }
const langLabelMap: Record<Language, string> = { en: 'English', es: 'Español', fr: 'Français', bg: 'Български' }

const langFlag = (lang: Language): string => langFlagMap[lang] || lang
const langLabel = (lang: Language): string => langLabelMap[lang] || lang

const onNameChange = (e: Event): void => {
  const val = (e.target as HTMLInputElement).value
  console.log('Name changed to:', val)
}
const onAddFile = (): void => {
  console.log('Add file to image:', image.value?.id)
}
const onReplaceFile = (index: number): void => {
  console.log('Replace file at index:', index)
}
const onDeleteFile = (index: number): void => {
  console.log('Delete file at index:', index)
}
const onUploadVoice = (language: Language): void => {
  console.log('Upload voice for language:', language)
}

const playingVoiceId = ref<string | null>(null)
let activeAudio: HTMLAudioElement | null = null

function hasAudio(voice: Voice): boolean {
  return !!(voice.audioUrl && voice.audioUrl.trim())
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
  const audio = new Audio(voice.audioUrl)
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
</style>
