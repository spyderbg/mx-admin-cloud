<template>
  <div v-if="category" style="width: 100%;">
    <div class="page-header" style="margin-bottom: 0.5rem;">
      <h3 class="page-title">{{ category.name }}</h3>
    </div>
    <CategoryDetail
      :category="category"
      @edit="onEditCategory"
      @delete="onDeleteCategory"
      @rename="onRenameCategory"
      @edit-voice="onEditVoice"
      @delete-voice="onDeleteVoice"
    />
    <div class="d-flex justify-content-between align-items-center mt-4 mb-3">
      <h4 class="mb-0">Images</h4>
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
        <button class="btn btn-primary btn-sm" @click="onAddImage">
          <i class="mdi mdi-plus mr-1"></i>Add New Image
        </button>
      </div>
    </div>
    <ImageGrid :images="category.images" :category-id="category.id" :columns="gridColumns" />
  </div>
  <div v-else class="text-center py-5">
    <h4 class="text-muted">Select a category from the sidebar</h4>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getCategories } from '../api/categories'
import type { Category, Voice } from '../types'
import CategoryDetail from '../components/CategoryDetail.vue'
import ImageGrid from '../components/ImageGrid.vue'

const route = useRoute()
const router = useRouter()

const gridColumns = ref(3)
const categories = ref<Category[]>([])
onMounted(async () => { categories.value = await getCategories() })

const category = computed(() => {
  const id = route.params.categoryId as string
  if (id) return categories.value.find(c => c.id === id) ?? null
  return categories.value[0] ?? null
})

watch(category, (cat) => {
  if (cat && !route.params.categoryId) {
    router.replace(`/categories/${cat.id}`)
  }
}, { immediate: true })

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
const onDeleteVoice = (voice: Voice): void => {
  console.log('Delete voice:', voice.id)
}
const onAddImage = (): void => {
  console.log('Add new image to category:', category.value?.id)
}
</script>
