<template>
  <nav class="sidebar sidebar-offcanvas" id="sidebar" :class="{ active: isActive }">
    <ul class="nav">
      <!-- User profile -->
      <li class="nav-item nav-profile">
        <a href="#" class="nav-link">
          <div class="profile-image">
            <img class="img-xs rounded-circle" :src="user.avatarUrl" alt="profile image" />
            <div class="dot-indicator bg-success"></div>
          </div>
          <div class="text-wrapper">
            <p class="profile-name">{{ user.name }}</p>
            <p class="designation">{{ user.role }}</p>
          </div>
        </a>
      </li>

      <!-- Filter -->
      <li class="nav-item" style="padding: 0 1.5rem 0.5rem;">
        <div style="position: relative;">
          <input
            type="text"
            class="form-control form-control-sm"
            placeholder="Filter..."
            v-model="filterText"
            style="background: rgba(255,255,255,0.15); border-color: rgba(255,255,255,0.2); color: #fff; padding-left: 2rem; padding-right: 2rem;"
          />
          <i
            class="mdi mdi-magnify"
            style="position: absolute; left: 8px; top: 50%; transform: translateY(-50%); color: rgba(255,255,255,0.5); font-size: 16px; pointer-events: none;"
          ></i>
          <i
            v-if="filterText"
            class="mdi mdi-close"
            style="position: absolute; right: 8px; top: 50%; transform: translateY(-50%); color: rgba(255,255,255,0.5); font-size: 16px; cursor: pointer;"
            @click="filterText = ''"
          ></i>
        </div>
      </li>

      <li class="nav-item nav-category">Categories</li>

      <!-- Category tree -->
      <li
        v-for="cat in filteredCategories"
        :key="cat.id"
        class="nav-item"
        :class="{ active: cat.id === activeCategoryId }"
      >
        <a
          class="nav-link"
          href="#"
          @click.prevent="toggleCategory(cat.id)"
          :aria-expanded="expandedIds.has(cat.id) ? 'true' : 'false'"
        >
          <i class="menu-icon mdi mdi-folder-image"></i>
          <span class="menu-title">{{ cat.name }}</span>
          <i class="menu-arrow"></i>
        </a>
        <div class="collapse" :class="{ show: expandedIds.has(cat.id) }">
          <ul class="nav flex-column sub-menu">
            <li
              v-for="img in cat.filteredImages"
              :key="img.id"
              class="nav-item"
            >
              <router-link
                class="nav-link"
                :to="`/categories/${cat.id}/images/${img.id}`"
                :class="{ active: img.id === activeImageId }"
              >
                {{ img.name }}
              </router-link>
            </li>
            <li v-if="cat.filteredImages.length === 0" class="nav-item">
              <span class="nav-link" style="opacity: 0.5; font-style: italic;">No matches</span>
            </li>
          </ul>
        </div>
      </li>

      <li v-if="filteredCategories.length === 0" class="nav-item">
        <span class="nav-link" style="opacity: 0.5; font-style: italic;">No categories match</span>
      </li>
    </ul>
  </nav>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { mockUser, mockCategories } from '../data/mockData'

defineProps<{
  isActive: boolean
}>()

const route = useRoute()
const router = useRouter()
const user = mockUser
const filterText = ref('')
const expandedIds = ref(new Set<string>())

const activeCategoryId = computed(() => route.params.categoryId as string || '')
const activeImageId = computed(() => route.params.imageId as string || '')

interface FilteredCategory {
  id: string
  name: string
  filteredImages: { id: string; name: string }[]
}

const filteredCategories = computed((): FilteredCategory[] => {
  const q = filterText.value.toLowerCase().trim()
  if (!q) {
    return mockCategories.map(c => ({
      id: c.id,
      name: c.name,
      filteredImages: c.images.map(i => ({ id: i.id, name: i.name })),
    }))
  }
  const result: FilteredCategory[] = []
  for (const cat of mockCategories) {
    const catMatch = cat.name.toLowerCase().includes(q)
    const matchedImages = cat.images.filter(i => i.name.toLowerCase().includes(q))
    if (catMatch || matchedImages.length > 0) {
      result.push({
        id: cat.id,
        name: cat.name,
        filteredImages: catMatch
          ? cat.images.map(i => ({ id: i.id, name: i.name }))
          : matchedImages.map(i => ({ id: i.id, name: i.name })),
      })
    }
  }
  return result
})

watch(filterText, (q) => {
  if (q.trim()) {
    for (const cat of filteredCategories.value) {
      expandedIds.value.add(cat.id)
    }
  }
})

watch(activeCategoryId, (id) => {
  if (id) expandedIds.value.add(id)
}, { immediate: true })

const toggleCategory = (id: string): void => {
  if (expandedIds.value.has(id)) {
    expandedIds.value.delete(id)
  } else {
    expandedIds.value.add(id)
  }
  router.push(`/categories/${id}`)
}
</script>
