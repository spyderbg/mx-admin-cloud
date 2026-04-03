<template>
  <div class="card mb-2" style="width: 100%;">
    <div class="card-body">
      <div class="row">
        <!-- Left: Category Name, Icon, Description -->
        <div class="col-lg-5 col-md-5 mb-3 mb-md-0">
          <div class="mb-3">
            <label class="text-muted small mb-1 d-block">Category Name</label>
            <div class="d-flex align-items-center">
              <h5 class="mb-0">{{ category.name }}</h5>
              <button class="btn btn-link btn-sm text-primary p-0 ml-2" title="Rename category" @click="openRenameModal">
                <i class="mdi mdi-pencil" style="font-size: 1rem;"></i>
              </button>
            </div>
          </div>
          <div class="mb-3">
            <label class="text-muted small mb-1 d-block">Category Icon</label>
            <div class="category-icon-wrap">
              <img
                :src="category.iconUrl"
                alt="Category icon"
                class="rounded"
              />
              <div class="category-icon-actions">
                <button
                  type="button"
                  class="btn-icon btn-icon-edit"
                  title="Upload category icon"
                  @click="showUpload = true"
                >
                  <i class="mdi mdi-pencil"></i>
                </button>
                <button
                  type="button"
                  class="btn-icon btn-icon-delete"
                  title="Delete category"
                  @click="showDelete = true"
                >
                  <i class="mdi mdi-delete"></i>
                </button>
              </div>
            </div>
          </div>
          <p class="text-muted mb-0" style="font-size: 0.9rem;">{{ category.description }}</p>
        </div>

        <!-- Right: Category Voices -->
        <div class="col-lg-7 col-md-7">
          <label class="text-muted small mb-2 d-block">Category Voices</label>
          <div class="voice-cards-grid">
            <div v-for="voice in category.voices" :key="voice.id" class="voice-card">
              <button
                class="voice-card-edit-icon"
                title="Upload voice file"
                @click="openVoiceUpload(voice)"
              >
                <i class="mdi mdi-pencil"></i>
              </button>
              <div class="voice-card-flag">
                <i
                  :class="'flag-icon flag-icon-' + langFlag(voice.language)"
                ></i>
              </div>
              <div class="voice-card-name">{{ voice.name }}</div>
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
              <button
                class="btn btn-primary btn-sm voice-card-btn"
                type="button"
                @click="openVoiceUpload(voice)"
              >
                Edit Voice
              </button>
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
          <h5 class="cd-modal-title">Rename category</h5>
          <button type="button" class="cd-modal-close" @click="showRename = false">&times;</button>
        </div>
        <div class="cd-modal-body">
          <div class="form-group mb-0">
            <label class="col-form-label">Category name</label>
            <input
              v-model="renameValue"
              type="text"
              class="form-control"
              placeholder="Enter category name"
              @keyup.enter="confirmRename"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="showRename = false">Cancel</button>
          <button type="button" class="btn btn-primary" @click="confirmRename">Save</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Delete confirmation modal -->
  <Teleport to="body">
    <div v-if="showDelete" class="cd-modal-overlay" @click.self="showDelete = false">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">Delete category</h5>
          <button type="button" class="cd-modal-close" @click="showDelete = false">&times;</button>
        </div>
        <div class="cd-modal-body">
          Are you sure you want to delete the category "{{ category.name }}"?
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="showDelete = false">Cancel</button>
          <button type="button" class="btn btn-danger" @click="showDelete = false; $emit('delete')">Delete</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Upload icon modal -->
  <Teleport to="body">
    <div v-if="showUpload" class="cd-modal-overlay" @click.self="showUpload = false">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">Upload category icon</h5>
          <button type="button" class="cd-modal-close" @click="showUpload = false">&times;</button>
        </div>
        <div class="cd-modal-body">
          <div class="form-group mb-0">
            <label class="col-form-label">Choose an image file</label>
            <input
              type="file"
              class="form-control-file"
              accept="image/*"
              @change="onFileSelected"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="showUpload = false">Cancel</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Upload voice file modal -->
  <Teleport to="body">
    <div v-if="showVoiceUpload" class="cd-modal-overlay" @click.self="closeVoiceUpload">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">Upload voice file</h5>
          <button type="button" class="cd-modal-close" @click="closeVoiceUpload">&times;</button>
        </div>
        <div class="cd-modal-body">
          <p v-if="editingVoice" class="text-muted small mb-2">{{ editingVoice.name }} ({{ editingVoice.language }})</p>
          <div class="form-group mb-0">
            <label class="col-form-label">Choose an audio file</label>
            <input
              type="file"
              class="form-control-file"
              accept="audio/*"
              @change="onVoiceFileSelected"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeVoiceUpload">Cancel</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { Category, Language, Voice } from '../types'

const props = defineProps<{
  category: Category
}>()

const emit = defineEmits<{
  edit: []
  delete: []
  rename: [name: string]
  'edit-voice': [voice: Voice]
  'delete-voice': [voice: Voice]
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
const editingVoice = ref<Voice | null>(null)
const renameValue = ref('')
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

function openVoiceUpload(voice: Voice): void {
  editingVoice.value = voice
  showVoiceUpload.value = true
}

function closeVoiceUpload(): void {
  showVoiceUpload.value = false
  editingVoice.value = null
}

function onVoiceFileSelected(e: Event): void {
  const input = e.target as HTMLInputElement
  if (input.files?.length && editingVoice.value) {
    console.log('Voice file selected:', editingVoice.value.id, input.files[0].name)
    emit('edit-voice', editingVoice.value)
    closeVoiceUpload()
    input.value = ''
  }
}

function openRenameModal(): void {
  renameValue.value = props.category.name
  showRename.value = true
}

function confirmRename(): void {
  const trimmed = renameValue.value.trim()
  if (trimmed && trimmed !== props.category.name) {
    emit('rename', trimmed)
  }
  showRename.value = false
}

function onFileSelected(e: Event): void {
  const input = e.target as HTMLInputElement
  if (input.files?.length) {
    console.log('Category icon file selected:', input.files[0].name)
    emit('edit')
    showUpload.value = false
  }
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
/* Category icon */
.category-icon-wrap {
  position: relative;
  width: 330px;
  max-width: 100%;
}
.category-icon-wrap img {
  width: 100%;
  height: 345px;
  object-fit: cover;
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
.btn-icon-delete {
  background: #dc2626;
}
.btn-icon-delete:hover {
  background: #b91c1c;
}

/* Voice cards grid */
.voice-cards-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.75rem;
}
.voice-card {
  position: relative;
  border: 1.5px solid #b8d4f0;
  border-radius: 10px;
  padding: 1rem 0.75rem 0.75rem;
  text-align: center;
  background: #f8fbff;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.voice-card-edit-icon {
  position: absolute;
  top: 6px;
  right: 6px;
  background: none;
  border: none;
  color: #6b7280;
  cursor: pointer;
  font-size: 14px;
  padding: 2px;
  line-height: 1;
}
.voice-card-edit-icon:hover {
  color: #4f46e5;
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
.voice-card-name {
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
.voice-card-btn {
  width: 100%;
  font-size: 0.75rem;
  padding: 0.25rem 0.5rem;
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
</style>
