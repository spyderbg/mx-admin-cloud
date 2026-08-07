<template>
  <div v-if="category" style="width: 100%;">
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
        <button class="btn btn-primary btn-sm" @click="onAddItem">
          <i class="mdi mdi-plus mr-1"></i>Add New Item
        </button>
      </div>
    </div>
    <ItemGrid :items="category.items" :category-id="category.id" :columns="gridColumns" />
  </div>
  <div v-else class="text-center py-5">
    <h4 class="text-muted">Select a category from the sidebar</h4>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ensureCategoriesLoaded,
  ensureCategoryDetail,
  invalidateCategoriesCache,
  sharedCategories,
  sharedCategoryNames,
} from '../api/cache'
import { readUiConfiguration, writeUiConfiguration } from '../api/uiConfigurationDb'
import { LocalizedString, SUPPORTED_LANGUAGES, type Language, type Voice } from '../types/viewModels'
import { categoriesRename } from '../api/contentClient'
import CategoryDetail from '../components/CategoryDetail.vue'
import ItemGrid from '../components/ItemGrid.vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import { localizedCategoryLabel, breadcrumbCategoriesLabel, itemsLabel } from '../utils/localizedLabels'
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

const onEditCategory = (): void => {
  console.log('Edit category:', category.value?.id)
}
const onDeleteCategory = (): void => {
  console.log('Delete category:', category.value?.id)
}
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
const onEditVoice = (voice: Voice, action: 'add' | 'update'): void => {
  console.log(`${action === 'add' ? 'Add' : 'Edit'} voice:`, voice.id)
}
const onRenameVoice = async (voice: Voice, name: string): Promise<void> => {
  await onRenameCategory(name, voice.language)
}
const onDeleteVoice = (voice: Voice): void => {
  console.log('Delete voice:', voice.id)
}
const onAddItem = (): void => {
  console.log('Add new item to category:', category.value?.id)
}
</script>
