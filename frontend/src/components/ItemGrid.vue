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
        :alt="itemLabel(item)"
        class="item-card-thumb"
      />
      <span v-if="!hasItemImage(item)" class="item-card-name">{{ itemLabel(item) }}</span>
      <span class="file-count-badge">{{ item.imagesCount ?? item.images.length }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { Item } from '../types/viewModels'
import cameraPlaceholderUrl from '../assets/images/samples/camera-placeholder.svg'
import { itemThumbnailVersions } from '../api/cache'
import { useContentLanguage } from '../composables/useContentLanguage'
import { localizedImageLabel } from '../utils/localizedLabels'
import { cacheBustedUri } from '../utils/cacheBustedUri'

const props = defineProps<{
  items: Item[]
  categoryId: string
  columns: number
}>()

const router = useRouter()
const contentLanguage = useContentLanguage()

const hasItemImage = (item: Item): boolean =>
  item.thumbnailUri.trim().length > 0 || item.images.some((image) => image.trim().length > 0)

const itemLabel = (item: Item): string =>
  localizedImageLabel(item, contentLanguage.value)

const resolveItemSrc = (item: Item): string => {
  const thumbnailVersion = itemThumbnailVersions.value.get(`${props.categoryId}:${item.id}`) ?? 0
  if (item.thumbnailUri.trim().length > 0) {
    return cacheBustedUri(item.thumbnailUri, thumbnailVersion)
  }

  const firstFile = item.images.find((image) => image.trim().length > 0)
  return firstFile ? cacheBustedUri(firstFile, thumbnailVersion) : cameraPlaceholderUrl
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
.item-card-name {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  padding: 0.45rem 0.6rem;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.86);
  color: #1e3a5f;
  font-size: 0.8rem;
  font-weight: 700;
  line-height: 1.2;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
  pointer-events: none;
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
</style>
