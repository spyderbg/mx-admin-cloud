<template>
  <div v-if="sharedLoading" class="text-center py-5">
    <div class="spinner-border text-primary" role="status"></div>
    <p class="mt-2 text-muted">{{ loadingCategoriesLabel(contentLanguage) }}</p>
  </div>
  <p v-else-if="sharedError && sharedCategories.length === 0" class="text-danger text-center py-5">{{ failedToLoadCategoriesLabel(contentLanguage) }}</p>
  <div v-else-if="category" style="width: 100%;">
    <nav aria-label="breadcrumb">
      <ol class="breadcrumb bg-transparent p-0 mb-3">
        <li class="breadcrumb-item">
          <a href="#" @click.prevent="goToDashboard">{{ breadcrumbRoot }}</a>
        </li>
        <li class="breadcrumb-item active" aria-current="page">{{ breadcrumbCategory }}</li>
      </ol>
    </nav>
    <div class="page-header" style="margin-bottom: 0.5rem;">
      <h3 class="page-title">{{ pageTitle }}</h3>
    </div>
    <CategoryDetail
      :category="category"
      @edit="onEditCategory"
      @delete="onDeleteCategory"
      @rename="onRenameCategory"
      @edit-voice="onEditVoice"
      @rename-voice="onRenameVoice"
      @delete-voice="onDeleteVoice"
    />
    <div class="d-flex justify-content-between align-items-center mt-4 mb-3">
      <h4 class="mb-0">{{ itemsLabel(contentLanguage) }}</h4>
      <div class="d-flex align-items-center" style="gap: 1rem;">
        <div class="d-flex align-items-center" style="gap: 0.5rem;">
          <i class="mdi mdi-view-grid-outline text-muted" style="font-size: 1.1rem;"></i>
          <input
            type="range"
            min="1"
            max="6"
            step="1"
            v-model.number="gridColumns"
            style="width: 100px; cursor: pointer;"
            :title="`${gridColumns} column${gridColumns > 1 ? 's' : ''}`"
          />
          <i class="mdi mdi-view-grid text-muted" style="font-size: 1.4rem;"></i>
        </div>
        <button class="btn btn-primary btn-sm" @click="openAddItemModal">
          <i class="mdi mdi-plus mr-1"></i>{{ addNewItemLabel(contentLanguage) }}
        </button>
      </div>
    </div>
    <ItemGrid :items="category.items" :category-id="category.id" :columns="gridColumns" />
  </div>
  <div v-else class="text-center py-5">
    <h4 class="text-muted">Select a category from the sidebar</h4>
  </div>

  <!-- Add new item modal -->
  <Teleport to="body">
    <div v-if="showAddItem" class="cd-modal-overlay" @click.self="closeAddItemModal">
      <div class="cd-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ addNewItemTitleLabel(contentLanguage) }}</h5>
          <button type="button" class="cd-modal-close" @click="closeAddItemModal">&times;</button>
        </div>
        <div class="cd-modal-body">
          <div class="form-group">
            <label class="col-form-label">{{ itemNameEnglishLabel(contentLanguage) }}</label>
            <input
              v-model="newItemWord"
              type="text"
              class="form-control"
              :placeholder="enterItemNameLabel(contentLanguage)"
              @keyup.enter="confirmAddItem"
            />
          </div>
          <div class="form-group mb-0">
            <label class="col-form-label">{{ imageFileLabel(contentLanguage) }}</label>
            <input
              type="file"
              class="form-control-file"
              accept="image/*"
              @change="onAddItemFileSelected"
            />
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeAddItemModal">{{ cancelLabel(contentLanguage) }}</button>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="!newItemWord.trim() || !newItemFile"
            @click="confirmAddItem"
          >
            {{ createLabel(contentLanguage) }}
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
      :class="toast.variant === 'error' ? 'cd-toast-error' : 'cd-toast-success'"
    >
      <i :class="toast.variant === 'error' ? 'mdi mdi-alert-circle' : 'mdi mdi-check-circle'"></i>
      <span>{{ toast.message }}</span>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { AxiosError } from 'axios'
import {
  ensureCategoriesLoaded,
  ensureCategoryDetail,
  invalidateCategoriesCache,
  sharedCategories,
  sharedCategoryNames,
  sharedLoading,
  sharedError,
} from '../api/cache'
import { readUiConfiguration, writeUiConfiguration } from '../api/uiConfigurationDb'
import { LocalizedString, SUPPORTED_LANGUAGES, type Language, type Voice } from '../types/viewModels'
import { categoriesRename, createItemEndpoint } from '../api/contentClient'
import CategoryDetail from '../components/CategoryDetail.vue'
import ItemGrid from '../components/ItemGrid.vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import {
  addNewItemLabel,
  addNewItemTitleLabel,
  cancelLabel,
  createLabel,
  enterItemNameLabel,
  failedToLoadCategoriesLabel,
  imageFileLabel,
  itemNameEnglishLabel,
  localizedCategoryLabel,
  breadcrumbCategoriesLabel,
  itemsLabel,
  loadingCategoriesLabel,
} from '../utils/localizedLabels'
import { toPascalCaseWords } from '../utils/pascalCaseWords'

const route = useRoute()
const router = useRouter()

const goToDashboard = (): void => {
  router.push({ name: 'dashboard' })
}

const contentLanguage = useContentLanguage()

const GRID_COLUMNS_CONFIG_KEY = 'categories.itemsGrid.columns'
const MIN_GRID_COLUMNS = 1
const MAX_GRID_COLUMNS = 6

const gridColumns = ref(3)

function normalizeGridColumns(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isInteger(value)) return null
  if (value < MIN_GRID_COLUMNS || value > MAX_GRID_COLUMNS) return null
  return value
}

function localizedStringForPatch(
  source: LocalizedString,
  update?: { language: Language; value: string },
): LocalizedString {
  const values = Object.fromEntries(
    SUPPORTED_LANGUAGES.map((language) => [
      language,
      update?.language === language
        ? update.value
        : source.get(language, source.en),
    ]),
  )
  return new LocalizedString(values)
}

onMounted(async () => {
  // Use shared store — deduplicates with DashboardView if it already fetched
  const storedColumns = await readUiConfiguration(GRID_COLUMNS_CONFIG_KEY).catch((error) => {
    console.warn('Failed to load item grid column preference:', error)
    return null
  })
  await ensureCategoriesLoaded()
  const normalizedColumns = normalizeGridColumns(storedColumns?.value)
  if (normalizedColumns !== null) gridColumns.value = normalizedColumns
})

const category = computed(() => {
  const id = route.params.categoryId as string
  if (id) return sharedCategories.value.find(c => c.id.toLowerCase() === id.toLowerCase()) ?? null
  return sharedCategories.value[0] ?? null
})

const pageTitle = computed(() => {
  const cat = category.value
  if (!cat) return ''
  return toPascalCaseWords(localizedCategoryLabel(cat, contentLanguage.value))
})

const breadcrumbRoot = computed(() => breadcrumbCategoriesLabel(contentLanguage.value))

const breadcrumbCategory = computed(() =>
  category.value ? localizedCategoryLabel(category.value, contentLanguage.value) : '',
)

// Auto-redirect from /categories to /categories/:id when a category is selected
watch(category, (cat) => {
  if (cat && !route.params.categoryId) {
    router.replace(`/categories/${cat.id}`)
  }
}, { immediate: true })

watch(
  () => route.params.categoryId as string,
  async (id) => {
    if (!id) return
    await ensureCategoriesLoaded()
    const name = sharedCategoryNames.value.get(id)
    if (!name) return
    await ensureCategoryDetail(id, name)
  },
  { immediate: true },
)

watch(gridColumns, (columns) => {
  void writeUiConfiguration(GRID_COLUMNS_CONFIG_KEY, columns).catch((error) => {
    console.warn('Failed to persist item grid column preference:', error)
  })
})

// CategoryDetail.vue already performs the API call, cache refresh, and cache-busting
// for icon edit/delete and voice edit/delete before emitting — these are notification-only.
const onEditCategory = (): void => {}
const onDeleteCategory = (): void => {}
const onRenameCategory = async (name: string, language?: Language): Promise<void> => {
  const currentCategory = category.value
  if (!currentCategory) return

  const categoryName = localizedStringForPatch(
    currentCategory.name,
    language ? { language, value: name } : undefined,
  )
  const description = localizedStringForPatch(currentCategory.description)
  const newCategoryId = language ? undefined : name

  try {
    const result = await categoriesRename(currentCategory.id, {
      new_category_id: newCategoryId,
      category_name: categoryName,
      description,
    })

    invalidateCategoriesCache()
    const categories = await ensureCategoriesLoaded()
    const targetCategoryId = result.category_id || newCategoryId || currentCategory.id
    const renamedCategory =
      categories.find((entry) => entry.id === targetCategoryId) ??
      categories.find((entry) => entry.id === currentCategory.id) ??
      categories.find((entry) => entry.name.en === categoryName.en) ??
      (language ? categories.find((entry) => localizedCategoryLabel(entry, language) === name) : undefined)

    if (renamedCategory) {
      await ensureCategoryDetail(renamedCategory.id, renamedCategory.name.en)
      if (route.params.categoryId !== renamedCategory.id) {
        await router.replace(`/categories/${renamedCategory.id}`)
      }
    }
  } catch (error) {
    console.error('Failed to rename category:', error)
  }
}
const onEditVoice = (): void => {}
const onRenameVoice = async (voice: Voice, name: string): Promise<void> => {
  await onRenameCategory(name, voice.language)
}
const onDeleteVoice = (): void => {}

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

const showAddItem = ref(false)
const newItemWord = ref('')
const newItemFile = ref<File | null>(null)

function openAddItemModal(): void {
  newItemWord.value = ''
  newItemFile.value = null
  showAddItem.value = true
}

function closeAddItemModal(): void {
  showAddItem.value = false
}

function onAddItemFileSelected(e: Event): void {
  const input = e.target as HTMLInputElement
  newItemFile.value = input.files?.[0] ?? null
}

async function confirmAddItem(): Promise<void> {
  const currentCategory = category.value
  const word = newItemWord.value.trim()
  const file = newItemFile.value
  if (!currentCategory || !word || !file) return

  try {
    const formData = new FormData()
    formData.append('category', currentCategory.name.en)
    formData.append('item', word)
    formData.append('image', file)
    const result = await createItemEndpoint(formData)

    showAddItem.value = false
    invalidateCategoriesCache()
    const categories = await ensureCategoriesLoaded()
    const created = categories.find((entry) => entry.id === currentCategory.id)
    const createdItem = created?.items.find((entry) => entry.id === result.item)

    showToast(`Item "${word}" created.`, 'success')
    if (created && createdItem) {
      await router.push(`/categories/${created.id}/items/${createdItem.id}`)
    }
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  }
}
</script>

<style scoped>
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
