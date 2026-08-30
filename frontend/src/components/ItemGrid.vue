<template>
  <div class="item-grid" :style="{ gridTemplateColumns: `repeat(${columns}, 1fr)` }">
    <div
      v-for="item in items"
      :key="item.id"
      class="item-card"
      @click="navigateToDetail(item.id)"
    >
      <img
        :src="resolveItemSrc(item)"
        :alt="item.name"
        class="item-card-thumb"
      />
      <span class="file-count-badge">{{ item.imagesCount ?? item.images.length }}</span>
      <button
        class="edit-badge"
        @click.stop="navigateToDetail(item.id)"
        :title="'Edit ' + item.name"
      >
        <i class="mdi mdi-pencil"></i>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { Item } from '../types/viewModels'
import cameraPlaceholderUrl from '../assets/images/samples/camera-placeholder.svg'

const props = defineProps<{
  items: Item[]
  categoryId: string
  columns: number
}>()

const router = useRouter()

const resolveItemSrc = (item: Item): string => {
  if (item.thumbnailUri.trim().length > 0) {
    return item.thumbnailUri
  }

  const firstFile = item.images.find((image) => image.trim().length > 0)
  return firstFile ?? cameraPlaceholderUrl
}

const navigateToDetail = (itemId: string): void => {
  router.push(`/categories/${props.categoryId}/items/${itemId}`)
}
</script>

<style scoped>
.item-grid {
  display: grid;
  gap: 1rem;
  width: 100%;
  transition: grid-template-columns 0.3s ease;
}
.item-card {
  position: relative;
  border-radius: 6px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
  transition: box-shadow 0.2s;
  cursor: pointer;
}
.item-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.18);
}
.item-card-thumb {
  width: 100%;
  aspect-ratio: 1/1;
  object-fit: cover;
  display: block;
}
.file-count-badge {
  position: absolute;
  top: 6px;
  right: 6px;
  min-width: 22px;
  height: 22px;
  padding: 0 5px;
  border-radius: 50%;
  background: #e53e3e;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  line-height: 22px;
  text-align: center;
  pointer-events: none;
}
.edit-badge {
  position: absolute;
  bottom: 8px;
  right: 8px;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #4f46e5;
  color: #fff;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  cursor: pointer;
  opacity: 0.9;
  transition: opacity 0.2s;
}
.edit-badge:hover {
  opacity: 1;
}
</style>
