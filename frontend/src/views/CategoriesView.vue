<template>
  <div v-if="category" style="width: 100%;">
    <nav aria-label="breadcrumb">
      <ol class="breadcrumb bg-transparent p-0 mb-3">
        <li class="breadcrumb-item">
          <router-link :to="{ name: 'dashboard' }">{{ breadcrumbRoot }}</router-link>
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
      <h4 class="mb-0">Items</h4>
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
import { ensureCategoriesLoaded, ensureCategoryFull, sharedCategories, sharedCategoryNames } from '../api/cache'
import type { Voice } from '../types/viewModels'
import CategoryDetail from '../components/CategoryDetail.vue'
import ItemGrid from '../components/ItemGrid.vue'
import { useContentLanguage } from '../composables/useContentLanguage'
import { localizedCategoryLabel, breadcrumbCategoriesLabel } from '../utils/localizedLabels'
import { toPascalCaseWords } from '../utils/pascalCaseWords'

const route = useRoute()
const router = useRouter()

const contentLanguage = useContentLanguage()

const gridColumns = ref(3)

onMounted(async () => {
  // Use shared store — deduplicates with DashboardView if it already fetched
  await ensureCategoriesLoaded()
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
    const name = sharedCategoryNames.value.get(id)
    if (!name) return
    await ensureCategoryFull(name)
  },
)

const onEditCategory = (): void => {
  console.log('Edit category:', category.value?.id)
}
const onDeleteCategory = (): void => {
  console.log('Delete category:', category.value?.id)
}
const onRenameCategory = (name: string): void => {
  console.log('Rename category:', category.value?.id, 'to', name)
}
const onEditVoice = (voice: Voice): void => {
  console.log('Edit voice:', voice.id)
}
const onRenameVoice = (voice: Voice, name: string): void => {
  console.log('Rename voice word:', voice.id, 'to', name)
}
const onDeleteVoice = (voice: Voice): void => {
  console.log('Delete voice:', voice.id)
}
const onAddItem = (): void => {
  console.log('Add new item to category:', category.value?.id)
}
</script>
