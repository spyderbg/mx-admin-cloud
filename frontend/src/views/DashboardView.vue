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

    <div v-if="archivedCategories.length" class="archived-section">
      <hr class="mb-3" />
      <button type="button" class="archived-toggle" @click="archivedExpanded = !archivedExpanded">
        <span class="d-flex align-items-center">
          <i class="mdi mdi-archive-outline mr-2 text-muted"></i>
          <strong>{{ archivedCategoriesLabel(contentLanguage) }}</strong>
          <span class="badge badge-pill badge-secondary ml-2">{{ archivedCategories.length }}</span>
        </span>
        <i class="mdi text-muted" :class="archivedExpanded ? 'mdi-chevron-up' : 'mdi-chevron-down'"></i>
      </button>

      <div v-if="archivedExpanded" class="archived-list">
        <div
          v-for="entry in archivedCategories"
          :key="entry.archiveFolder"
          class="archived-row d-flex align-items-center justify-content-between"
        >
          <div class="d-flex align-items-center">
            <i class="mdi mdi-archive text-muted mr-3" style="font-size: 1.5rem;"></i>
            <div>
              <div class="font-weight-bold">{{ entry.baseName }}</div>
              <small class="text-muted">{{ archivedAgoLabel(contentLanguage, entry.archivedAt) }}</small>
            </div>
          </div>
          <button
            class="btn btn-outline-secondary btn-sm"
            :disabled="restoringFolder !== ''"
            @click="restoreCategory(entry)"
          >
            <span
              v-if="restoringFolder === entry.archiveFolder"
              class="spinner-border spinner-border-sm mr-1"
              role="status"
            ></span>
            <i v-else class="mdi mdi-restore mr-1"></i>{{ restoreLabel(contentLanguage) }}
          </button>
        </div>
      </div>
    </div>
  </div>

  <AddCategoryModal
    :show="showAddCategory"
    :existing-folder-names="existingFolderNames"
    @close="showAddCategory = false"
    @created="onCategoryCreated"
  />

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
  ensureCategoriesLoaded,
  invalidateCategoriesCache,
  sharedArchivedCategories,
  sharedCategories,
  sharedLoading,
  sharedError,
} from '../api/cache'
import { categoriesRestore } from '../api/contentClient'
import AddCategoryModal from '../components/AddCategoryModal.vue'
import type { ArchivedCategory } from '../types/viewModels'
import { categoryIdFromFolderName, folderNameFromCategoryId } from '../utils/categoryIds'
import {
  addNewCategoryLabel,
  archivedAgoLabel,
  archivedCategoriesLabel,
  breadcrumbCategoriesLabel,
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
    iconUri: c.thumbnail_uri ?? cameraPlaceholderUrl,
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

async function restoreCategory(entry: ArchivedCategory): Promise<void> {
  if (restoringFolder.value) return
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
  margin-top: 0.5rem;
}
.archived-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 0.5rem 0;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
}
.archived-list {
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 12px;
  overflow: hidden;
}
.archived-row {
  padding: 1rem 1.25rem;
}
.archived-row + .archived-row {
  border-top: 1px solid rgba(0, 0, 0, 0.08);
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
