<template>
  <nav class="view-breadcrumb" aria-label="breadcrumb">
    <ol class="breadcrumb bg-transparent p-0">
      <li
        v-for="(item, index) in items"
        :key="`${index}-${item.label}`"
        class="breadcrumb-item"
        :class="{ active: isActive(index) }"
        :aria-current="isActive(index) ? 'page' : undefined"
      >
        <RouterLink v-if="item.to && !isActive(index)" :to="item.to">{{ item.label }}</RouterLink>
        <span v-else>{{ item.label }}</span>
      </li>
    </ol>
  </nav>
</template>

<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'

export interface ViewBreadcrumbItem {
  label: string
  to?: RouteLocationRaw
}

const props = defineProps<{
  items: ViewBreadcrumbItem[]
}>()

const isActive = (index: number): boolean => index === props.items.length - 1
</script>

<style scoped>
.view-breadcrumb {
  min-height: 1.5rem;
  margin-bottom: 1rem;
}
.view-breadcrumb .breadcrumb {
  align-items: center;
  font-size: 0.82rem;
  font-weight: 600;
  line-height: 1.2;
  margin-bottom: 0;
  min-height: 1.5rem;
}
.view-breadcrumb .breadcrumb-item {
  display: inline-flex;
  align-items: center;
  min-height: 1.5rem;
}
</style>
