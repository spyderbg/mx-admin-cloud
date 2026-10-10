<template>
  <div class="voice-cards-grid">
    <div
      v-for="voice in voices"
      :key="voice.id"
      class="voice-card"
      :class="{ 'voice-card-drop-active': dragOverVoiceId === voice.id }"
      @dragenter.prevent="onVoiceDragEnter(voice)"
      @dragover.prevent="onVoiceDragOver(voice)"
      @dragleave.prevent="onVoiceDragLeave($event, voice)"
      @drop.prevent="onVoiceDrop($event, voice)"
    >
      <div v-if="!readonly" class="voice-card-top-actions">
        <button
          v-for="action in extraActions"
          :key="action.title"
          class="btn-icon btn-icon-regenerate voice-card-action"
          type="button"
          :title="action.title"
          :aria-label="action.title"
          :disabled="action.isDisabled?.(voice)"
          @click="action.onClick(voice)"
        >
          <i class="mdi" :class="action.isLoading?.(voice) ? 'mdi-loading mdi-spin' : action.icon"></i>
        </button>
        <button
          class="btn-icon btn-icon-edit voice-card-action"
          type="button"
          :title="uploadVoiceFileLabel(contentLanguage)"
          :aria-label="uploadVoiceFileLabel(contentLanguage)"
          @click="openUpload(voice)"
        >
          <i class="mdi mdi-pencil"></i>
        </button>
        <button
          type="button"
          class="btn-icon btn-icon-delete voice-card-action"
          :title="deleteOrResetVoiceLabel(contentLanguage)"
          :aria-label="deleteOrResetVoiceLabel(contentLanguage)"
          @click="openDeleteConfirm(voice)"
        >
          <i class="mdi mdi-delete"></i>
        </button>
      </div>
      <div class="voice-card-flag" :title="langLabel(voice.language, contentLanguage)" :aria-label="langLabel(voice.language, contentLanguage)">
        <i :class="'flag-icon flag-icon-' + langFlag(voice.language)"></i>
      </div>
      <ServerWord
        v-if="renameVoice && !readonly"
        :text="voice.name"
        text-tag="div"
        text-class="voice-card-name"
        root-class="justify-content-center"
        :edit-title="editWordLabel(contentLanguage)"
        :icon-style="{ fontSize: '0.8rem' }"
        @edit="openRenameModal(voice)"
      />
      <div v-else class="voice-card-name">{{ voice.name }}</div>
      <VoiceParametersTooltip class="voice-card-type" :provider="voice.provider" :parameters="voice.parameters" />
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
          <h5 class="cd-modal-title">{{ uploadVoiceFileLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeUpload">&times;</button>
        </div>
        <div class="cd-modal-body">
          <p v-if="uploadTarget" class="text-muted small mb-2">{{ uploadTarget.name }} ({{ uploadTarget.language }})</p>
          <div class="form-group mb-0">
            <label class="col-form-label">{{ audioFileLabel(contentLanguage) }}</label>
            <div class="localized-file-picker">
              <input
                ref="uploadFileInputRef"
                type="file"
                class="localized-file-input"
                accept="audio/*"
                aria-hidden="true"
                tabindex="-1"
                @change="onFileSelected"
              />
              <button type="button" class="btn btn-outline-primary btn-sm" @click="openFilePicker">
                <i class="mdi mdi-paperclip mr-1"></i>{{ chooseFileLabel(contentLanguage) }}
              </button>
              <span class="localized-file-status">{{ noFileSelectedLabel(contentLanguage) }}</span>
            </div>
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeUpload">{{ cancelLabel(contentLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Delete / reset voice confirmation -->
  <DeleteFileDialog
    v-if="showDeleteConfirm && deleteTarget"
    :title="deleteOrResetVoiceLabel(contentLanguage)"
    :message="deleteOrResetVoiceConfirmLabel(contentLanguage, deleteTarget.name, deleteTarget.language)"
    :help="deleteOrResetVoiceHelpLabel(contentLanguage)"
    :confirm-label="deleteResetLabel(contentLanguage)"
    @cancel="closeDeleteConfirm"
    @confirm="confirmDelete"
  />

  <!-- Edit voice word (label) modal -->
  <Teleport to="body">
    <div v-if="showRenameEdit" class="cd-modal-overlay" @click.self="closeRenameModal">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ editWordLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeRenameModal">&times;</button>
        </div>
        <div class="cd-modal-body">
          <p v-if="renameTarget" class="text-muted small mb-2">
            <i :class="'flag-icon flag-icon-' + langFlag(renameTarget.language)" class="mr-1"></i>
            {{ renameTarget.language.toUpperCase() }}
          </p>
          <div class="form-group mb-0">
            <label class="col-form-label">{{ wordColumnLabel(contentLanguage) }}</label>
            <input
              v-model="renameValue"
              type="text"
              class="form-control"
              :placeholder="enterWordLabel(contentLanguage)"
              @keyup.enter="confirmRename"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeRenameModal">{{ cancelLabel(contentLanguage) }}</button>
          <button type="button" class="btn btn-primary" @click="confirmRename">{{ saveLabel(contentLanguage) }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ServerWord from './ServerWord.vue'
import VoiceParametersTooltip from './VoiceParametersTooltip.vue'
import DeleteFileDialog from './DeleteFileDialog.vue'
import type { Voice } from '../types/viewModels'
import { langFlag, langLabel } from '../utils/voiceLabels'
import { useContentLanguage } from '../composables/useContentLanguage'
import {
  audioFileLabel,
  chooseFileLabel,
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
  noFileSelectedLabel,
  uploadVoiceFileLabel,
  voiceFileMissingLabel,
  wordColumnLabel,
} from '../utils/localizedLabels'

export interface VoiceExtraAction {
  icon: string
  title: string
  onClick: (voice: Voice) => void
  /** Show a spinner in place of the icon for this voice while true. */
  isLoading?: (voice: Voice) => boolean
  /** Disable the button (e.g. while any voice is mid-action) while true. */
  isDisabled?: (voice: Voice) => boolean
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
  /** Rendered before the built-in upload/delete icons; omit for none. */
  extraActions?: VoiceExtraAction[]
  /** Only play voices: no upload, delete, rename, extra actions or drop-to-upload. */
  readonly?: boolean
}>()

const contentLanguage = useContentLanguage()

const showUpload = ref(false)
const uploadTarget = ref<Voice | null>(null)
const uploadFileInputRef = ref<HTMLInputElement | null>(null)

function openUpload(voice: Voice): void {
  uploadTarget.value = voice
  showUpload.value = true
}

function closeUpload(): void {
  showUpload.value = false
  uploadTarget.value = null
  if (uploadFileInputRef.value) uploadFileInputRef.value.value = ''
}

function openFilePicker(): void {
  uploadFileInputRef.value?.click()
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && showUpload.value) {
    closeUpload()
  }
}

onMounted(() => {
  document.addEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onDocumentKeydown)
})

function onFileSelected(e: Event): void {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  const voice = uploadTarget.value
  closeUpload()
  input.value = ''
  if (!file || !voice) return
  void props.uploadVoice(voice, file, !props.hasAudio(voice))
}

const dragOverVoiceId = ref<string | null>(null)

function onVoiceDragEnter(voice: Voice): void {
  if (props.readonly) return
  dragOverVoiceId.value = voice.id
}

function onVoiceDragOver(voice: Voice): void {
  if (props.readonly) return
  dragOverVoiceId.value = voice.id
}

function onVoiceDragLeave(event: DragEvent, voice: Voice): void {
  if (dragOverVoiceId.value !== voice.id) return
  const dropTarget = event.currentTarget
  if (!(dropTarget instanceof HTMLElement) || !dropTarget.contains(event.relatedTarget as Node | null)) {
    dragOverVoiceId.value = null
  }
}

function onVoiceDrop(event: DragEvent, voice: Voice): void {
  dragOverVoiceId.value = null
  if (props.readonly) return
  const file = Array.from(event.dataTransfer?.files ?? []).find((entry) => entry.type.startsWith('audio/'))
  if (!file) return
  void props.uploadVoice(voice, file, !props.hasAudio(voice))
}

const showDeleteConfirm = ref(false)
const deleteTarget = ref<Voice | null>(null)

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
  transition: border-color 0.15s, background-color 0.15s;
}
.voice-card-drop-active {
  border-color: #2563eb;
  background: #dbeafe;
}
.voice-card-top-actions {
  position: absolute;
  top: 0.45rem;
  right: 0.45rem;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.3rem;
  max-width: calc(100% - 0.9rem);
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
.btn-icon:disabled {
  cursor: not-allowed;
  opacity: 0.6;
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
.localized-file-picker {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.localized-file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}
.localized-file-status {
  color: #4b5563;
  font-size: 0.9rem;
  line-height: 1.2;
}
.cd-modal-footer {
  padding: 0.75rem 1.25rem;
  border-top: 1px solid #dee2e6;
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
