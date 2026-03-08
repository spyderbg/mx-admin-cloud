<template>
  <div class="row">
    <div
      v-for="image in images"
      :key="image.id"
      class="col-xl-3 col-lg-4 col-md-6 col-sm-6 col-12 mb-4"
    >
      <div class="image-card" @click="navigateToDetail(image.id)">
        <img
          :src="image.thumbnailUrl"
          :alt="image.name"
          class="img-fluid rounded"
          style="width: 100%; aspect-ratio: 4/3; object-fit: cover; cursor: pointer;"
        />
        <button
          class="edit-badge"
          @click.stop="navigateToDetail(image.id)"
          :title="'Edit ' + image.name"
        >
          <i class="mdi mdi-pencil"></i>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { Image } from '../types'

const props = defineProps<{
  images: Image[]
  categoryId: string
}>()

const router = useRouter()

const navigateToDetail = (imageId: string): void => {
  router.push(`/categories/${props.categoryId}/images/${imageId}`)
}
</script>

<style scoped>
.image-card {
  position: relative;
  border-radius: 6px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
  transition: box-shadow 0.2s;
}
.image-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.18);
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
