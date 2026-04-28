<template>
  <div class="image-grid" :style="{ gridTemplateColumns: `repeat(${columns}, 1fr)` }">
    <div
      v-for="image in images"
      :key="image.id"
      class="image-card"
      @click="navigateToDetail(image.id)"
    >
      <img
        :src="resolveImageSrc(image)"
        :alt="image.name"
        class="image-card-thumb"
      />
      <span class="file-count-badge">{{ image.files.length }}</span>
      <button
        class="edit-badge"
        @click.stop="navigateToDetail(image.id)"
        :title="'Edit ' + image.name"
      >
        <i class="mdi mdi-pencil"></i>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { Image } from '../types'
import cameraPlaceholderUrl from '../assets/images/samples/camera-placeholder.svg'

const props = defineProps<{
  images: Image[]
  categoryId: string
  columns: number
}>()

const router = useRouter()

const resolveImageSrc = (image: Image): string => {
  if (image.thumbnailUrl.trim().length > 0) {
    return image.thumbnailUrl
  }

  const firstFile = image.files.find((file) => file.trim().length > 0)
  return firstFile ?? cameraPlaceholderUrl
}

const navigateToDetail = (imageId: string): void => {
  router.push(`/categories/${props.categoryId}/images/${imageId}`)
}
</script>

<style scoped>
.image-grid {
  display: grid;
  gap: 1rem;
  width: 100%;
  transition: grid-template-columns 0.3s ease;
}
.image-card {
  position: relative;
  border-radius: 6px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
  transition: box-shadow 0.2s;
  cursor: pointer;
}
.image-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.18);
}
.image-card-thumb {
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
