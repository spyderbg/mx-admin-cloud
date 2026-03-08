<template>
  <div v-if="category">
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
      <button class="btn btn-primary btn-sm" @click="onAddImage">
        <i class="mdi mdi-plus mr-1"></i>Add New Image
      </button>
    </div>
    <ImageGrid :images="category.images" :category-id="category.id" />
  </div>
  <div v-else class="text-center py-5">
    <h4 class="text-muted">Select a category from the sidebar</h4>
  </div>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { mockCategories } from '../data/mockData'
import type { Voice } from '../types'
import CategoryDetail from '../components/CategoryDetail.vue'
import ImageGrid from '../components/ImageGrid.vue'

const route = useRoute()
const router = useRouter()

const category = computed(() => {
  const id = route.params.categoryId as string
  if (id) return mockCategories.find(c => c.id === id) || null
  return mockCategories[0] || null
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
