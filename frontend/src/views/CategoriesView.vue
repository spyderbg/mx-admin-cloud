<template>
  <div v-if="sharedLoading" class="text-center py-5">
    <div class="spinner-border text-primary" role="status"></div>
    <p class="mt-2 text-muted">{{ loadingCategoriesLabel(contentLanguage) }}</p>
  </div>
  <p v-else-if="sharedError && sharedCategories.length === 0" class="text-danger text-center py-5">{{ categoriesLoadErrorLabel(contentLanguage, sharedContentUnavailable) }}</p>
  <div v-else-if="category" style="width: 100%;">
    <ViewBreadcrumb :items="breadcrumbItems" />
    <CategoryDetail
      :category="category"
      :archiving="archiving"
      @edit="onEditCategory"
      @delete="onDeleteCategory"
      @rename="onRenameCategory"
      @archive="onArchiveCategory"
      @rename-voice="onRenameVoice"
    />
    <section class="category-items-panel">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h4 class="mb-0">{{ itemsLabel(contentLanguage) }}</h4>
        <div class="d-flex align-items-center" style="gap: 1rem;">
          <div class="d-flex align-items-center" style="gap: 0.5rem;">
            <i class="mdi mdi-view-grid-outline text-muted" style="font-size: 1.1rem;"></i>
            <input
              type="range"
              min="1"
              max="7"
              step="1"
              v-model.number="gridColumns"
              style="width: 100px; cursor: pointer;"
              :title="`${gridColumns} column${gridColumns > 1 ? 's' : ''}`"
            />
            <i class="mdi mdi-view-grid text-muted" style="font-size: 1.4rem;"></i>
          </div>
          <button v-if="canEdit" class="btn btn-primary btn-sm" @click="openAddItemModal">
            <i class="mdi mdi-plus mr-1"></i>{{ addNewItemLabel(contentLanguage) }}
          </button>
        </div>
      </div>
      <ItemGrid :items="category.items" :category-id="category.id" :columns="gridColumns" />
    </section>
  </div>
  <div v-else class="text-center py-5">
    <h4 class="text-muted">Select a category from the sidebar</h4>
  </div>

  <AddItemModal
    :show="showAddItem"
    :category-id="category?.id ?? ''"
    :existing-item-ids="existingItemIds"
    @close="showAddItem = false"
    @created="onItemCreated"
  />

  <!-- Result toast -->
  <Teleport to="body">
    <div
      v-if="toast"
      class="cd-toast"
      :class="`cd-toast-${toast.variant}`"
    >
      <i :class="toast.variant === 'success' ? 'mdi mdi-check-circle' : 'mdi mdi-alert-circle'"></i>
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
  sharedContentUnavailable,
  sharedLoading,
  sharedError,
} from '../api/cache'
import { readUiConfiguration, writeUiConfiguration } from '../api/uiConfigurationDb'
import { LocalizedString, SUPPORTED_LANGUAGES, type Language, type Voice } from '../types/viewModels'
import { categoriesRemove, categoriesUpdate } from '../api/contentClient'
import type { CreateItemResponse } from '../types/contentSchemas'
import AddItemModal from '../components/AddItemModal.vue'
import CategoryDetail from '../components/CategoryDetail.vue'
import ItemGrid from '../components/ItemGrid.vue'
import ViewBreadcrumb from '../components/ViewBreadcrumb.vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import {
  addNewItemLabel,
  categoriesLoadErrorLabel,
  itemCreatedLabel,
  itemCreatedWithWarningsLabel,
  localizedCategoryLabel,
  breadcrumbCategoriesLabel,
  itemsLabel,
  loadingCategoriesLabel,
} from '../utils/localizedLabels'
import { toPascalCaseWords } from '../utils/pascalCaseWords'
import { hasWriteAccess } from '../utils/permissions'

const route = useRoute()
const router = useRouter()

const contentLanguage = useContentLanguage()
const canEdit = hasWriteAccess()

const GRID_COLUMNS_CONFIG_KEY = 'categories.itemsGrid.columns'
const MIN_GRID_COLUMNS = 1
const MAX_GRID_COLUMNS = 7

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

const breadcrumbRoot = computed(() => breadcrumbCategoriesLabel(contentLanguage.value))

const breadcrumbCategory = computed(() =>
  category.value ? toPascalCaseWords(localizedCategoryLabel(category.value, contentLanguage.value)) : '',
)

const breadcrumbItems = computed(() => [
  { label: breadcrumbRoot.value, to: { name: 'dashboard' } },
  { label: breadcrumbCategory.value },
])

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
    const result = await categoriesUpdate(currentCategory.id, {
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
const archiving = ref(false)

const onArchiveCategory = async (): Promise<void> => {
  const currentCategory = category.value
  if (!currentCategory || archiving.value) return

  archiving.value = true
  try {
    const result = await categoriesRemove(currentCategory.id)
    // 'removed' means ARCHIVE_DIR was unset server-side and the content is gone for good.
    showToast(result.message, result.archived ? 'success' : 'warning')

    invalidateCategoriesCache()
    await ensureCategoriesLoaded()
    await router.replace({ name: 'dashboard' })
  } catch (err) {
    showToast(extractErrorMessage(err), 'error')
  } finally {
    archiving.value = false
  }
}

const onRenameVoice = async (voice: Voice, name: string): Promise<void> => {
  await onRenameCategory(name, voice.language)
}

type ToastVariant = 'success' | 'error' | 'warning'
const toast = ref<{ message: string; variant: ToastVariant } | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showToast(message: string, variant: ToastVariant, durationMs = 4000): void {
  if (toastTimer) clearTimeout(toastTimer)
  toast.value = { message, variant }
  toastTimer = setTimeout(() => { toast.value = null }, durationMs)
}

function extractErrorMessage(err: unknown): string {
  const axiosErr = err as AxiosError<{ detail?: string }>
  return axiosErr.response?.data?.detail || 'The operation failed. Please try again.'
}

const showAddItem = ref(false)
const existingItemIds = computed(() => category.value?.items.map((item) => item.id) ?? [])

function openAddItemModal(): void {
  showAddItem.value = true
}

// AddItemModal.vue performs the request and reports errors inline; this only refreshes
// the cache and reports the outcome. Voice problems never block creation, so they arrive
// as warnings on a successful response.
async function onItemCreated(result: CreateItemResponse, word: string): Promise<void> {
  showAddItem.value = false
  const categoryId = category.value?.id
  const hasVoiceWarnings = result.voice_warnings.length > 0

  invalidateCategoriesCache()
  await ensureCategoriesLoaded()

  if (hasVoiceWarnings) {
    // Stay on the category page: the toast lives in this view and would vanish on navigation,
    // and the warning is the only place the user learns that voices are missing.
    showToast(
      `${itemCreatedWithWarningsLabel(contentLanguage.value, word)} ${result.voice_warnings.join(' ')}`,
      'warning',
      8000,
    )
    return
  }

  showToast(itemCreatedLabel(contentLanguage.value, word), 'success')
  if (categoryId) {
    await router.push(`/categories/${categoryId}/items/${result.item_id}`)
  }
}
</script>

<style scoped>
.category-items-panel {
  width: 100%;
  background: #fff;
  border: 1px solid #e6eaf4;
  border-radius: 8px;
  box-shadow: 0 4px 18px rgba(25, 38, 77, 0.04);
  margin: 1rem 0;
  padding: 1.25rem 1.5rem;
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
