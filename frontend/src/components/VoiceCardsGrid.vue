<template>
  <div class="voice-cards-grid">
    <div v-for="voice in voices" :key="voice.id" class="voice-card">
      <div class="voice-card-top-actions">
        <button
          v-if="extraAction"
          class="btn-icon btn-icon-regenerate voice-card-action"
          type="button"
          :title="extraAction.title"
          :aria-label="extraAction.title"
          @click="extraAction.onClick(voice)"
        >
          <i :class="'mdi ' + extraAction.icon"></i>
        </button>
        <button
          class="btn-icon btn-icon-edit voice-card-action"
          type="button"
          title="Upload voice"
          aria-label="Upload voice"
          @click="openUpload(voice)"
        >
          <i class="mdi mdi-pencil"></i>
        </button>
        <button
          type="button"
          class="btn-icon btn-icon-delete voice-card-action"
          :title="deleteOrResetVoiceLabel(voice.language)"
          :aria-label="deleteOrResetVoiceLabel(voice.language)"
          @click="openDeleteConfirm(voice)"
        >
          <i class="mdi mdi-delete"></i>
        </button>
      </div>
      <div class="voice-card-flag" :title="langLabel(voice.language)" :aria-label="langLabel(voice.language)">
        <i :class="'flag-icon flag-icon-' + langFlag(voice.language)"></i>
      </div>
      <ServerWord
        v-if="renameVoice"
        :text="voice.name"
        text-tag="div"
        text-class="voice-card-name"
        root-class="justify-content-center"
        :edit-title="editWordLabel(voice.language)"
        :icon-style="{ fontSize: '0.8rem' }"
        @edit="openRenameModal(voice)"
      />
      <div v-else class="voice-card-name">{{ voice.name }}</div>
      <div class="voice-card-type">{{ voiceTypeLabel(voice.type) }}</div>
      <div class="voice-card-controls">
        <button
          class="btn-play"
          :class="{ playing: playingVoiceId === voice.id, disabled: !hasAudio(voice) }"
          type="button"
          :disabled="!hasAudio(voice)"
          @click="togglePlay(voice)"
          :title="!hasAudio(voice) ? voiceFileMissingLabel(contentLanguage) : (playingVoiceId === voice.id ? stopLabel(contentLanguage) : playLabel(contentLanguage))"
        >
          <i :class="playingVoiceId === voice.id ? 'mdi mdi-stop-circle' : 'mdi mdi-play-circle'"></i>
          <span>{{ playingVoiceId === voice.id ? stopLabel(contentLanguage) : playLabel(contentLanguage) }}</span>
        </button>
      </div>
    </div>
  </div>

  <!-- Upload voice file modal -->
  <Teleport to="body">
    <div v-if="showUpload" class="cd-modal-overlay" @click.self="closeUpload">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ uploadVoiceFileLabel(uploadLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeUpload">&times;</button>
        </div>
        <div class="cd-modal-body">
          <p v-if="uploadTarget" class="text-muted small mb-2">{{ uploadTarget.name }} ({{ uploadTarget.language }})</p>
          <div class="form-group mb-0">
            <label class="col-form-label">{{ audioFileLabel(uploadLanguage) }}</label>
            <input
              type="file"
              class="form-control-file"
              accept="audio/*"
              @change="onFileSelected"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeUpload">{{ cancelLabel(uploadLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Delete / reset voice confirmation -->
  <Teleport to="body">
    <div v-if="showDeleteConfirm" class="cd-modal-overlay" @click.self="closeDeleteConfirm">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ deleteOrResetVoiceLabel(deleteLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeDeleteConfirm">&times;</button>
        </div>
        <div class="cd-modal-body">
          <template v-if="deleteTarget">
            <p class="mb-2">
              {{ deleteOrResetVoiceConfirmLabel(deleteLanguage, deleteTarget.name, deleteTarget.language) }}
            </p>
            <p class="text-muted small mb-0">
              {{ deleteOrResetVoiceHelpLabel(deleteLanguage) }}
            </p>
          </template>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeDeleteConfirm">{{ cancelLabel(deleteLanguage) }}</button>
          <button type="button" class="btn btn-danger" @click="confirmDelete">{{ deleteResetLabel(deleteLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Edit voice word (label) modal -->
  <Teleport to="body">
    <div v-if="showRenameEdit" class="cd-modal-overlay" @click.self="closeRenameModal">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ editWordLabel(renameLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeRenameModal">&times;</button>
        </div>
        <div class="cd-modal-body">
          <p v-if="renameTarget" class="text-muted small mb-2">
            <i :class="'flag-icon flag-icon-' + langFlag(renameLanguage)" class="mr-1"></i>
            {{ renameLanguage.toUpperCase() }}
          </p>
          <div class="form-group mb-0">
            <label class="col-form-label">{{ wordColumnLabel(renameLanguage) }}</label>
            <input
              v-model="renameValue"
              type="text"
              class="form-control"
              :placeholder="enterWordLabel(renameLanguage)"
              @keyup.enter="confirmRename"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeRenameModal">{{ cancelLabel(renameLanguage) }}</button>
          <button type="button" class="btn btn-primary" @click="confirmRename">{{ saveLabel(renameLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import ServerWord from './ServerWord.vue'
import type { Language, Voice } from '../types/viewModels'
import { langFlag, langLabel, voiceTypeLabel } from '../utils/voiceLabels'
import { useContentLanguage } from '../composables/useContentLanguage'
import {
  audioFileLabel,
  cancelLabel,
  deleteOrResetVoiceConfirmLabel,
  deleteOrResetVoiceHelpLabel,
  deleteOrResetVoiceLabel,
  deleteResetLabel,
  editWordLabel,
  enterWordLabel,
  playLabel,
  saveLabel,
  stopLabel,
  uploadVoiceFileLabel,
  voiceFileMissingLabel,
  wordColumnLabel,
} from '../utils/localizedLabels'

export interface VoiceExtraAction {
  icon: string
  title: string
  onClick: (voice: Voice) => void
}

const props = defineProps<{
  voices: Voice[]
  playingVoiceId: string | null
  hasAudio: (voice: Voice) => boolean
  togglePlay: (voice: Voice) => void
  uploadVoice: (voice: Voice, file: File, wasAdd: boolean) => void | Promise<void>
  deleteVoice: (voice: Voice) => void | Promise<void>
  /** Omit to render the voice name as plain text (no inline edit). */
  renameVoice?: (voice: Voice, name: string) => void
  /** Omit to hide the top-left action icon entirely. */
  extraAction?: VoiceExtraAction
}>()

const contentLanguage = useContentLanguage()

const showUpload = ref(false)
const uploadTarget = ref<Voice | null>(null)
const uploadLanguage = computed<Language>(() => uploadTarget.value?.language ?? contentLanguage.value)

function openUpload(voice: Voice): void {
  uploadTarget.value = voice
  showUpload.value = true
}

function closeUpload(): void {
  showUpload.value = false
  uploadTarget.value = null
}

function onFileSelected(e: Event): void {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  const voice = uploadTarget.value
  closeUpload()
  input.value = ''
  if (!file || !voice) return
  void props.uploadVoice(voice, file, !props.hasAudio(voice))
}

const showDeleteConfirm = ref(false)
const deleteTarget = ref<Voice | null>(null)
const deleteLanguage = computed<Language>(() => deleteTarget.value?.language ?? contentLanguage.value)

function openDeleteConfirm(voice: Voice): void {
  deleteTarget.value = voice
  showDeleteConfirm.value = true
}

function closeDeleteConfirm(): void {
  showDeleteConfirm.value = false
  deleteTarget.value = null
}

function confirmDelete(): void {
  const voice = deleteTarget.value
  closeDeleteConfirm()
  if (!voice) return
  void props.deleteVoice(voice)
}

const showRenameEdit = ref(false)
const renameTarget = ref<Voice | null>(null)
const renameValue = ref('')
const renameLanguage = computed<Language>(() => renameTarget.value?.language ?? contentLanguage.value)

function openRenameModal(voice: Voice): void {
  renameTarget.value = voice
  renameValue.value = voice.name
  showRenameEdit.value = true
}

function closeRenameModal(): void {
  showRenameEdit.value = false
  renameTarget.value = null
}

function confirmRename(): void {
  const voice = renameTarget.value
  const trimmed = renameValue.value.trim()
  closeRenameModal()
  if (voice && trimmed && trimmed !== voice.name) {
    props.renameVoice?.(voice, trimmed)
  }
}
</script>

<style scoped>
.voice-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(7.5rem, 1fr));
  gap: 0.75rem;
}
.voice-card {
  position: relative;
  min-width: 0;
  min-height: 14rem;
  border: 1.5px solid #b8d4f0;
  border-radius: 10px;
  padding: 3rem 0.75rem 0.75rem;
  text-align: center;
  background: #f8fbff;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: space-between;
}
.voice-card-top-actions {
  position: absolute;
  top: 0.45rem;
  right: 0.45rem;
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
  margin-bottom: 0;
}
.voice-card-flag .flag-icon {
  font-size: 1.6rem;
}
:deep(.voice-card-name),
.voice-card-name {
  font-weight: 600;
  font-size: 0.85rem;
  color: #1e293b;
  margin-bottom: 0;
  line-height: 1.2;
  overflow-wrap: anywhere;
}
.voice-card-type {
  font-size: 0.75rem;
  color: #6b7280;
  margin-bottom: 0;
}
.voice-card-controls {
  margin-bottom: 0;
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
