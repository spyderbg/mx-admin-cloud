<template>
  <article class="collection-card" :class="{ 'collection-card-expanded': expanded }">
    <RouterLink v-if="to" :to="to" class="collection-card-link">
      <div class="collection-card-summary">
        <img v-if="imageSrc" :src="imageSrc" :alt="title" class="collection-card-image" />
        <span v-else class="collection-card-symbol"><slot name="icon"><i class="mdi mdi-folder-outline" aria-hidden="true"></i></slot></span>
        <div class="collection-card-copy">
          <h5 :title="title">{{ title }}</h5>
          <p v-if="description">{{ description }}</p>
        </div>
      </div>
    </RouterLink>
    <slot v-else name="header" />
    <slot />
  </article>
</template>

<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'

defineProps<{
  title?: string
  description?: string
  imageSrc?: string
  to?: RouteLocationRaw
  expanded?: boolean
}>()
</script>

<style scoped>
.collection-card { min-width: 0; overflow: hidden; background: #fff; border: 1px solid rgba(0, 0, 0, .12); border-radius: 12px; box-shadow: 0 2px 8px rgba(0, 0, 0, .06); transition: box-shadow .2s, border-color .2s; }
.collection-card:has(.collection-card-link):hover { box-shadow: 0 4px 16px rgba(0, 0, 0, .12); }
.collection-card-link { display: block; height: 100%; color: inherit; text-decoration: none; }
.collection-card-link:hover { color: inherit; background: #f8f9fa; text-decoration: none; }
.collection-card-link:focus-visible { outline: 2px solid #2364ed; outline-offset: -2px; border-radius: 12px; }
.collection-card-summary { display: flex; align-items: center; gap: 1rem; padding: 1.5rem; }
.collection-card-image, .collection-card-symbol { width: 64px; height: 64px; flex: none; border-radius: 10px; }
.collection-card-image { object-fit: cover; }
.collection-card-symbol { display: grid; place-items: center; background: #edf3ff; color: #2563eb; font-size: 2rem; }
.collection-card-copy { flex: 1; min-width: 0; }
.collection-card-copy h5 { margin: 0 0 .25rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.collection-card-copy p { margin: 0; color: #6c757d; font-size: .875rem; }
.collection-card-expanded { border: 2px solid #2364ed; box-shadow: 0 8px 28px rgba(34, 72, 151, .12); }
</style>
