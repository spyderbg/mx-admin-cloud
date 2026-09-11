<template>
  <div class="dashboard">
    <!-- Kept outside the loading/error/empty branches below so it stays reachable
         when there are no categories yet — the state where it matters most. -->
    <div class="page-header d-flex justify-content-between align-items-center">
      <h3 class="page-title mb-0">{{ pageTitle }}</h3>
      <button class="btn btn-primary btn-sm" @click="showAddCategory = true">
        <i class="mdi mdi-plus mr-1"></i>{{ addNewCategoryLabel(contentLanguage) }}
      </button>
    </div>

    <div v-if="loading" class="text-center py-5">
      <div class="spinner-border text-primary" role="status"></div>
      <p class="mt-2 text-muted">{{ loadingCategoriesLabel(contentLanguage) }}</p>
    </div>

    <p v-else-if="error" class="text-danger text-center py-5">{{ failedToLoadCategoriesLabel(contentLanguage) }}</p>

    <div v-else-if="cards.length === 0" class="text-center py-5">
      <p class="text-muted">{{ noCategoriesFoundLabel(contentLanguage) }}</p>
    </div>

    <div v-else class="row">
      <div
        v-for="card in cards"
        :key="card.id"
        class="col-md-6 col-lg-4 col-xl-3 mb-4"
      >
        <div
          class="cd-card card-hover"
          style="border: 1px solid rgba(0,0,0,0.12); border-radius: 12px; overflow: hidden; cursor: pointer; transition: box-shadow 0.2s, transform 0.2s;"
          @mouseover="hoverCard(card.id, true)"
          @mouseleave="hoverCard(card.id, false)"
          @click="goToCategory(card.id)"
        >
          <div
            class="d-flex align-items-center p-4"
            :class="hoveredCard === card.id ? 'bg-light' : ''"
          >
            <img
              :src="card.iconUri"
              :alt="card.name"
              class="cd-card-icon mr-3"
              style="width: 64px; height: 64px; border-radius: 10px; object-fit: cover; flex-shrink: 0;"
            />
            <div class="flex-grow-1">
              <h5 class="mb-1 text-truncate" :title="card.name">{{ card.name }}</h5>
              <p class="text-muted mb-0" style="font-size: 0.875rem;">
                {{ itemCountLabel(contentLanguage, card.itemCount) }}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <section v-if="archivedCategories.length" class="archived-section">
      <button
        type="button"
        class="archived-toggle"
        :aria-expanded="archivedExpanded"
        @click="archivedExpanded = !archivedExpanded"
      >
        <span class="archived-toggle-copy">
          <span class="archived-toggle-title">
            <span class="archived-toggle-marker">
              <i class="mdi mdi-archive"></i>
            </span>
            <strong>{{ archivedCategoriesLabel(contentLanguage) }}</strong>
            <span class="archived-count">{{ archivedCategories.length }}</span>
          </span>
          <span class="archived-subtitle">{{ archivedDescriptionLabel() }}</span>
        </span>
        <i class="mdi archived-toggle-chevron" :class="archivedExpanded ? 'mdi-chevron-up' : 'mdi-chevron-down'"></i>
      </button>

      <div v-if="archivedExpanded" class="archived-list">
        <div
          v-for="entry in archivedCategories"
          :key="entry.archiveFolder"
          class="archived-row"
        >
          <div class="archived-row-main">
            <img
              :src="archivedThumbnail(entry)"
              :alt="archivedDisplayName(entry)"
              class="archived-thumbnail"
            />
            <div class="archived-row-copy">
              <div class="archived-row-name">{{ archivedDisplayName(entry) }}</div>
              <div class="archived-row-items">
                <span>{{ itemCountLabel(contentLanguage, archivedItemCount(entry)) }}</span>
              </div>
            </div>
          </div>
          <div class="archived-meta">
            <span class="archived-meta-label">{{ archivedCreatedColumnLabel() }}</span>
            <span class="archived-meta-value">{{ archivedCreatedDateLabel(entry) }}</span>
          </div>
          <div class="archived-meta">
            <span class="archived-meta-label">{{ archivedDateColumnLabel() }}</span>
            <span class="archived-meta-value">{{ archivedDateLabel(entry) }}</span>
          </div>
          <div class="archived-row-actions">
            <button
              class="archived-action archived-action-restore"
              :disabled="restoringFolder !== '' || deletingFolder !== ''"
              @click="restoreCategory(entry)"
            >
              <span
                v-if="restoringFolder === entry.archiveFolder"
                class="spinner-border spinner-border-sm"
                role="status"
              ></span>
              <template v-else>
                <i class="mdi mdi-refresh"></i>
                <span>{{ restoreLabel(contentLanguage) }}</span>
              </template>
            </button>
            <button
              class="archived-action archived-action-delete"
              :disabled="restoringFolder !== '' || deletingFolder !== ''"
              @click="openDeleteArchivedCategory(entry)"
            >
              <span
                v-if="deletingFolder === entry.archiveFolder"
                class="spinner-border spinner-border-sm"
                role="status"
              ></span>
              <template v-else>
                <i class="mdi mdi-delete-outline"></i>
                <span>{{ deleteLabel(contentLanguage) }}</span>
              </template>
            </button>
          </div>
        </div>
      </div>
    </section>
  </div>

  <AddCategoryModal
    :show="showAddCategory"
    :existing-folder-names="existingFolderNames"
    @close="showAddCategory = false"
    @created="onCategoryCreated"
  />

  <!-- Delete archived category confirmation modal -->
  <Teleport to="body">
    <div v-if="deleteTarget" class="cd-modal-overlay" @click.self="closeDeleteArchivedCategory">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ deleteLabel(contentLanguage) }} archived category</h5>
          <button type="button" class="cd-modal-close" @click="closeDeleteArchivedCategory">&times;</button>
        </div>
        <div class="cd-modal-body">
          <p class="mb-3">
            This will permanently delete the archived category "{{ deleteTarget.baseName }}".
          </p>
          <div class="alert alert-warning py-2">
            <small>
              <i class="mdi mdi-alert mr-1"></i>
              This cannot be undone and the category will no longer be restorable.
            </small>
          </div>
          <div class="form-group mb-0">
            <label class="col-form-label">
              {{ archiveTypeToConfirmLabel(contentLanguage, deleteTargetCategoryId) }}
            </label>
            <input
              v-model="deleteConfirmText"
              type="text"
              class="form-control"
              :placeholder="deleteTargetCategoryId"
              @keyup.enter="confirmDeleteArchivedCategory"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeDeleteArchivedCategory">{{ cancelLabel(contentLanguage) }}</button>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="!canDeleteArchivedCategory || deletingFolder !== ''"
            @click="confirmDeleteArchivedCategory"
          >
            <span
              v-if="deletingFolder === deleteTarget.archiveFolder"
              class="spinner-border spinner-border-sm mr-1"
              role="status"
            ></span>
            {{ deleteLabel(contentLanguage) }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Result toast -->
  <Teleport to="body">
    <div
      v-if="toast"
      class="cd-toast"
      :class="`cd-toast-${toast.variant}`"
    >
      <i :class="toast.variant === 'error' ? 'mdi mdi-alert-circle' : 'mdi mdi-check-circle'"></i>
      <span>{{ toast.message }}</span>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import type { AxiosError } from 'axios'
import { useContentLanguage } from '../composables/useContentLanguage'
import {
  categoryIconVersions,
  ensureCategoriesLoaded,
  invalidateCategoriesCache,
  sharedArchivedCategories,
  sharedCategories,
  sharedLoading,
  sharedError,
} from '../api/cache'
import { categoriesArchiveDelete, categoriesRestore } from '../api/contentClient'
import { cacheBustedUri } from '../utils/cacheBustedUri'
import AddCategoryModal from '../components/AddCategoryModal.vue'
import type { ArchivedCategory } from '../types/viewModels'
import { categoryIdFromFolderName, folderNameFromCategoryId } from '../utils/categoryIds'
import {
  addNewCategoryLabel,
  archivedCategoriesLabel,
  archiveTypeToConfirmLabel,
  breadcrumbCategoriesLabel,
  cancelLabel,
  deleteLabel,
  failedToLoadCategoriesLabel,
  itemCountLabel,
  loadingCategoriesLabel,
  localizedCategoryLabel,
  noCategoriesFoundLabel,
  restoreLabel,
} from '../utils/localizedLabels'
import { toPascalCaseWords } from '../utils/pascalCaseWords'
import cameraPlaceholderUrl from '../assets/images/samples/camera-placeholder.svg'


const router = useRouter()
const contentLanguage = useContentLanguage()
const loading = sharedLoading
const error = sharedError
const hoveredCard = ref('')

const pageTitle = computed(() =>
  toPascalCaseWords(breadcrumbCategoriesLabel(contentLanguage.value)),
)

const hoverCard = (id: string, isHovered: boolean): void => {
  hoveredCard.value = isHovered ? id : ''
}

const goToCategory = (id: string): void => {
  router.push(`/categories/${id}`)
}

const cards = computed(() => {
  const lang = contentLanguage.value
  return sharedCategories.value.map((c) => ({
    id: c.id,
    name: localizedCategoryLabel(c, lang),
    iconUri: c.thumbnail_uri
      ? cacheBustedUri(c.thumbnail_uri, categoryIconVersions.value.get(c.id) ?? 0)
      : cameraPlaceholderUrl,
    itemCount: c.items_count ?? 0,
  }))
})

onMounted(async () => {
  await ensureCategoriesLoaded()
})

type ToastVariant = 'success' | 'error' | 'warning'
const toast = ref<{ message: string; variant: ToastVariant } | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showToast(message: string, variant: ToastVariant): void {
  if (toastTimer) clearTimeout(toastTimer)
  toast.value = { message, variant }
  toastTimer = setTimeout(() => { toast.value = null }, 4000)
}

const showAddCategory = ref(false)

const existingFolderNames = computed(() =>
  sharedCategories.value.map((c) => folderNameFromCategoryId(c.id)),
)

async function onCategoryCreated(folderName: string, message: string): Promise<void> {
  showAddCategory.value = false
  showToast(message, 'success')
  invalidateCategoriesCache()
  const categories = await ensureCategoriesLoaded()
  const created = categories.find((c) => c.id === categoryIdFromFolderName(folderName))
  if (created) await router.push(`/categories/${created.id}`)
}

const archivedCategories = computed(() => sharedArchivedCategories.value)
const archivedExpanded = ref(false)
const restoringFolder = ref('')
const deletingFolder = ref('')
const deleteTarget = ref<ArchivedCategory | null>(null)
const deleteConfirmText = ref('')

function archivedDisplayName(entry: ArchivedCategory): string {
  return entry.baseName.replace(/_/g, ' ')
}

function archivedThumbnail(entry: ArchivedCategory): string {
  return entry.thumbnailUri || cameraPlaceholderUrl
}

function archivedItemCount(entry: ArchivedCategory): number {
  return entry.itemsCount ?? 0
}

function archivedDescriptionLabel(): string {
  const labels: Record<string, string> = {
    en: 'Categories that are archived and hidden from the main list.',
    es: 'Categorías archivadas y ocultas de la lista principal.',
    fr: 'Catégories archivées et masquées de la liste principale.',
    bg: 'Категории, които са архивирани и скрити от основния списък.',
  }
  return labels[contentLanguage.value] ?? labels.en
}

function archivedDateColumnLabel(): string {
  const labels: Record<string, string> = {
    en: 'Archived',
    es: 'Archivada',
    fr: 'Archivée',
    bg: 'Архивирана',
  }
  return labels[contentLanguage.value] ?? labels.en
}

function archivedCreatedColumnLabel(): string {
  const labels: Record<string, string> = {
    en: 'Created',
    es: 'Creada',
    fr: 'Créée',
    bg: 'Създадена',
  }
  return labels[contentLanguage.value] ?? labels.en
}

function archivedDateLabel(entry: ArchivedCategory): string {
  return new Intl.DateTimeFormat(contentLanguage.value, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(new Date(entry.archivedAt * 1000))
}

function archivedCreatedDateLabel(entry: ArchivedCategory): string {
  const timestamp = entry.createdAt ?? entry.archivedAt
  return new Intl.DateTimeFormat(contentLanguage.value, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(new Date(timestamp * 1000))
}

const deleteTargetCategoryId = computed(() =>
  deleteTarget.value ? categoryIdFromFolderName(deleteTarget.value.baseName) : '',
)

const canDeleteArchivedCategory = computed(() =>
  Boolean(deleteTarget.value && deleteConfirmText.value.trim() === deleteTargetCategoryId.value),
)

async function restoreCategory(entry: ArchivedCategory): Promise<void> {
  if (restoringFolder.value || deletingFolder.value) return
  restoringFolder.value = entry.archiveFolder
  try {
    // The full archive folder targets this exact snapshot rather than the newest one.
    const result = await categoriesRestore({ name: entry.archiveFolder })
    showToast(result.message, 'success')
    invalidateCategoriesCache()
    const categories = await ensureCategoriesLoaded()
    const restored = categories.find(
      (c) => folderNameFromCategoryId(c.id) === (result.name ?? entry.baseName),
    )
    if (restored) await router.push(`/categories/${restored.id}`)
  } catch (err) {
    const axiosErr = err as AxiosError<{ detail?: string }>
    showToast(
      axiosErr.response?.data?.detail || 'Could not restore the category. Please try again.',
      'error',
    )
  } finally {
    restoringFolder.value = ''
  }
}

function openDeleteArchivedCategory(entry: ArchivedCategory): void {
  if (restoringFolder.value || deletingFolder.value) return
  deleteTarget.value = entry
  deleteConfirmText.value = ''
}

function closeDeleteArchivedCategory(): void {
  if (deletingFolder.value) return
  deleteTarget.value = null
  deleteConfirmText.value = ''
}

async function confirmDeleteArchivedCategory(): Promise<void> {
  const entry = deleteTarget.value
  if (!entry || !canDeleteArchivedCategory.value) return
  if (restoringFolder.value || deletingFolder.value) return

  deletingFolder.value = entry.archiveFolder
  try {
    const result = await categoriesArchiveDelete(entry.archiveFolder)
    showToast(result.message, 'success')
    deleteTarget.value = null
    deleteConfirmText.value = ''
    invalidateCategoriesCache()
    await ensureCategoriesLoaded()
  } catch (err) {
    const axiosErr = err as AxiosError<{ detail?: string }>
    showToast(
      axiosErr.response?.data?.detail || 'Could not delete the archived category. Please try again.',
      'error',
    )
  } finally {
    deletingFolder.value = ''
  }
}
</script>

<style scoped>
.card-hover {
  background: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.card-hover:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}
.archived-section {
  margin-top: 1.25rem;
  padding: 1.5rem;
  background: #fff;
  border: 1px solid #dfe5ef;
  border-radius: 8px;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.04);
}
.archived-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 0;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
  color: #111827;
}
.archived-toggle-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.35rem;
}
.archived-toggle-title {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-width: 0;
  font-size: 1.125rem;
  line-height: 1.2;
}
.archived-toggle-marker {
  width: 1.625rem;
  height: 1.625rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #3157ff;
  font-size: 1.45rem;
  flex-shrink: 0;
}
.archived-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.55rem;
  height: 1.55rem;
  padding: 0 0.45rem;
  border-radius: 999px;
  background: #eef1f6;
  color: #111827;
  font-size: 0.8rem;
  font-weight: 700;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.08);
}
.archived-subtitle {
  margin-left: 2.375rem;
  color: #475569;
  font-size: 0.875rem;
  line-height: 1.35;
}
.archived-toggle-chevron {
  color: #475569;
  font-size: 1.45rem;
  flex-shrink: 0;
}
.archived-list {
  margin-top: 1.55rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.archived-row {
  display: grid;
  grid-template-columns: minmax(16rem, 1fr) 11rem 11rem auto;
  align-items: center;
  gap: 1.5rem;
  min-height: 7.5rem;
  padding: 1.25rem 1.35rem;
  border: 1px solid #dfe5ef;
  border-radius: 6px;
  background: #fff;
}
.archived-row-main {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 1.25rem;
}
.archived-thumbnail {
  width: 5rem;
  height: 5rem;
  border-radius: 8px;
  object-fit: cover;
  flex-shrink: 0;
  background: #f1f5f9;
}
.archived-row-copy {
  min-width: 0;
}
.archived-row-name {
  margin-bottom: 0.75rem;
  color: #111827;
  font-size: 1.08rem;
  font-weight: 700;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.archived-row-items {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  color: #475569;
  font-size: 0.875rem;
  line-height: 1.2;
}
.archived-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.65rem;
  min-width: 0;
}
.archived-meta-label {
  color: #475569;
  font-size: 0.85rem;
  line-height: 1.2;
}
.archived-meta-value {
  color: #111827;
  font-size: 0.98rem;
  line-height: 1.25;
  white-space: nowrap;
}
.archived-row-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.archived-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.45rem;
  box-sizing: border-box;
  inline-size: 8.75rem;
  min-height: 2.75rem;
  padding: 0.55rem 1rem;
  border: 1px solid;
  border-radius: 6px;
  background: #fff;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  transition: background-color 0.2s ease, border-color 0.2s ease, color 0.2s ease;
}
.archived-action:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}
.archived-action i {
  font-size: 1.1rem;
}
.archived-action-restore {
  border-color: #cfd8e7;
  color: #3157ff;
}
.archived-action-restore:hover:not(:disabled) {
  border-color: #3157ff;
  background: #f4f7ff;
}
.archived-action-delete {
  border-color: #ffb7bd;
  color: #ff303d;
}
.archived-action-delete:hover:not(:disabled) {
  border-color: #ff303d;
  background: #fff5f6;
}
@media (max-width: 991px) {
  .archived-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .archived-row-main,
  .archived-row-actions {
    grid-column: 1 / -1;
  }
  .archived-row-actions {
    justify-content: flex-start;
  }
}
@media (max-width: 767px) {
  .archived-section {
    padding: 1rem;
  }
  .archived-subtitle {
    margin-left: 0;
  }
  .archived-row {
    grid-template-columns: 1fr;
    gap: 1rem;
    padding: 1rem;
  }
  .archived-row-actions,
  .archived-meta {
    grid-column: auto;
    grid-row: auto;
  }
  .archived-row-actions {
    justify-content: flex-start;
  }
  .archived-action {
    flex: 1 1 8.75rem;
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
  width: 100%;
  max-width: 480px;
  background: #fff;
  border-radius: 4px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.25);
  overflow: hidden;
}
.cd-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid rgba(0, 0, 0, 0.12);
}
.cd-modal-title {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 600;
}
.cd-modal-close {
  border: none;
  background: transparent;
  font-size: 1.5rem;
  line-height: 1;
  cursor: pointer;
  color: #6b7280;
}
.cd-modal-body {
  padding: 1.25rem;
}
.cd-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.75rem 1.25rem;
  border-top: 1px solid rgba(0, 0, 0, 0.12);
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
.cd-toast-warning {
  background: #d97706;
}
</style>
