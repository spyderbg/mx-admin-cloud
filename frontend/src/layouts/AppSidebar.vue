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

      <li class="nav-item nav-category">
        <div
          class="d-flex align-items-center justify-content-between flex-nowrap"
          style="gap: 0.35rem;"
        >
          <span class="text-truncate">Categories</span>
          <div class="d-flex align-items-center flex-shrink-0" style="gap: 2px;">
            <button
              type="button"
              class="btn btn-sm border-0 rounded-0 p-1 cd-sidebar-tree-btn"
              title="Expand all categories"
              aria-label="Expand all categories"
              @click.stop="expandAllCategories"
            >
              <i class="mdi mdi-unfold-more-horizontal" aria-hidden="true"></i>
            </button>
            <button
              type="button"
              class="btn btn-sm border-0 rounded-0 p-1 cd-sidebar-tree-btn"
              title="Collapse all categories"
              aria-label="Collapse all categories"
              @click.stop="collapseAllCategories"
            >
              <i class="mdi mdi-unfold-less-horizontal" aria-hidden="true"></i>
            </button>
          </div>
        </div>
      </li>

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
                @click="requestCloseSidebar"
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
import { useContentLanguage } from '../composables/useContentLanguage'
import { localizedCategoryLabel, localizedImageLabel } from '../utils/localizedLabels'

defineProps<{
  isActive: boolean
}>()

const emit = defineEmits<{
  'close-sidebar': []
}>()

const requestCloseSidebar = (): void => {
  emit('close-sidebar')
}

const route = useRoute()
const router = useRouter()
const user = mockUser
const contentLanguage = useContentLanguage()
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
  const lang = contentLanguage.value
  const q = filterText.value.toLowerCase().trim()
  if (!q) {
    return mockCategories.map((c) => ({
      id: c.id,
      name: localizedCategoryLabel(c, lang),
      filteredImages: c.images.map((i) => ({
        id: i.id,
        name: localizedImageLabel(i, lang),
      })),
    }))
  }
  const result: FilteredCategory[] = []
  for (const cat of mockCategories) {
    const catLabel = localizedCategoryLabel(cat, lang)
    const catMatch = catLabel.toLowerCase().includes(q)
    const matchedImages = cat.images.filter((i) =>
      localizedImageLabel(i, lang).toLowerCase().includes(q),
    )
    if (catMatch || matchedImages.length > 0) {
      result.push({
        id: cat.id,
        name: catLabel,
        filteredImages: catMatch
          ? cat.images.map((i) => ({ id: i.id, name: localizedImageLabel(i, lang) }))
          : matchedImages.map((i) => ({ id: i.id, name: localizedImageLabel(i, lang) })),
      })
    }
  }
  return result
})

watch(filterText, (q) => {
  if (q.trim()) {
    const next = new Set(expandedIds.value)
    for (const cat of filteredCategories.value) {
      next.add(cat.id)
    }
    expandedIds.value = next
  }
})

watch(activeCategoryId, (id) => {
  if (!id) return
  const next = new Set(expandedIds.value)
  next.add(id)
  expandedIds.value = next
}, { immediate: true })

const expandAllCategories = (): void => {
  expandedIds.value = new Set(mockCategories.map((c) => c.id))
}

const collapseAllCategories = (): void => {
  expandedIds.value = new Set()
}

const toggleCategory = (id: string): void => {
  const next = new Set(expandedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  expandedIds.value = next
  router.push(`/categories/${id}`)
  requestCloseSidebar()
}
</script>

<style scoped>
.cd-sidebar-tree-btn {
  color: rgba(255, 255, 255, 0.65);
  background: transparent;
  line-height: 1;
  min-width: 1.75rem;
}
.cd-sidebar-tree-btn:hover,
.cd-sidebar-tree-btn:focus {
  color: #fff;
  background: rgba(255, 255, 255, 0.12);
  box-shadow: none;
}

/* Theme slides off-canvas sidebar from the right; slide from the left instead. */
@media (max-width: 991px) {
  nav.sidebar.sidebar-offcanvas {
    right: auto !important;
    left: -270px;
    transition:
      left 0.28s cubic-bezier(0.4, 0, 0.2, 1),
      box-shadow 0.28s ease;
  }
  nav.sidebar.sidebar-offcanvas.active {
    right: auto !important;
    left: 0;
    box-shadow: 8px 0 28px rgba(0, 0, 0, 0.2);
  }
}
</style>
