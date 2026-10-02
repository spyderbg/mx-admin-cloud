<template>
  <nav class="sidebar sidebar-offcanvas" id="sidebar" :class="{ active: isActive }" :aria-label="isConfigurationNavigation ? 'Service configurations' : 'Categories'">
    <ul class="nav">
      <!-- User profile -->
      <li class="nav-item nav-profile">
        <a href="#" class="nav-link">
          <div class="profile-image">
            <img class="img-xs rounded-circle" :src="user.avatarUri" alt="profile image" />
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
            class="form-control form-control-sm cd-sidebar-filter-input"
            :placeholder="filterLabel(contentLanguage)"
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
          <RouterLink v-if="isConfigurationNavigation" class="text-truncate cd-sidebar-root-link" :to="{ name: 'configuration' }" @click="requestCloseSidebar">Configuration</RouterLink>
          <span v-else class="text-truncate">{{ breadcrumbCategoriesLabel(contentLanguage) }}</span>
          <div class="d-flex align-items-center flex-shrink-0" style="gap: 2px;">
            <button
              type="button"
              class="btn btn-sm border-0 rounded-0 p-1 cd-sidebar-tree-btn"
              :title="isConfigurationNavigation ? 'Expand all services' : 'Expand all categories'"
              :aria-label="isConfigurationNavigation ? 'Expand all services' : 'Expand all categories'"
              @click.stop="expandAllNodes"
            >
              <i class="mdi mdi-unfold-more-horizontal" aria-hidden="true"></i>
            </button>
            <button
              type="button"
              class="btn btn-sm border-0 rounded-0 p-1 cd-sidebar-tree-btn"
              :title="isConfigurationNavigation ? 'Collapse all services' : 'Collapse all categories'"
              :aria-label="isConfigurationNavigation ? 'Collapse all services' : 'Collapse all categories'"
              @click.stop="collapseAllNodes"
            >
              <i class="mdi mdi-unfold-less-horizontal" aria-hidden="true"></i>
            </button>
          </div>
        </div>
      </li>

      <li class="nav-item cd-sidebar-tree">
        <ul class="nav cd-sidebar-tree-list">
          <template v-if="isConfigurationNavigation">
            <li v-if="configurationServicesLoading" class="nav-item"><span class="nav-link cd-sidebar-status" role="status">Loading services…</span></li>
            <li v-else-if="configurationServicesError" class="nav-item">
              <span class="nav-link cd-sidebar-status" role="alert">{{ configurationServicesError }}</span>
              <button class="btn btn-sm cd-sidebar-retry" type="button" @click="loadConfigurationTree">Try again</button>
            </li>
            <template v-else>
              <li v-for="service in filteredServices" :key="service.id" class="nav-item" :class="{ active: service.id === activeServiceName }">
                <RouterLink class="nav-link" :to="{ name: 'service-configuration', params: { serviceName: service.id } }" :aria-expanded="expandedServices.has(service.id)" :aria-current="service.id === activeServiceName && !activeConfigurationName ? 'page' : undefined" @click="toggleService(service.id)">
                  <i class="menu-icon mdi mdi-tune" aria-hidden="true"></i>
                  <span class="menu-title">{{ service.name }}</span>
                  <i class="menu-arrow" aria-hidden="true"></i>
                </RouterLink>
                <div class="collapse" :class="{ show: expandedServices.has(service.id) }">
                  <ul class="nav flex-column sub-menu">
                    <li v-if="serviceConfigurationLoading[service.id]" class="nav-item"><span class="nav-link cd-sidebar-status">Loading configurations…</span></li>
                    <li v-else-if="serviceConfigurationErrors[service.id]" class="nav-item">
                      <button type="button" class="nav-link cd-sidebar-retry" @click="retryService(service.id)">Retry loading configurations</button>
                    </li>
                    <template v-else>
                      <li v-for="file in service.files" :key="file.config_name" class="nav-item">
                        <RouterLink class="nav-link" :to="{ name: 'service-configuration', params: { serviceName: service.id }, query: { config: file.config_name } }" :class="{ active: service.id === activeServiceName && file.config_name === activeConfigurationName }" :aria-current="service.id === activeServiceName && file.config_name === activeConfigurationName ? 'page' : undefined" @click="requestCloseSidebar">
                          <span class="text-truncate" :title="file.filename">{{ file.filename }}</span>
                        </RouterLink>
                      </li>
                      <li v-if="!service.files.length" class="nav-item"><span class="nav-link cd-sidebar-status">{{ filterText.trim() ? 'No matching configurations' : 'No configurations' }}</span></li>
                    </template>
                  </ul>
                </div>
              </li>
              <li v-if="!filteredServices.length" class="nav-item"><span class="nav-link cd-sidebar-status">No services match</span></li>
            </template>
          </template>
          <template v-else>
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
              :aria-current="cat.id === activeCategoryId ? 'true' : undefined"
            >
              <i class="menu-icon mdi mdi-folder-image"></i>
              <span class="menu-title">{{ cat.name }}</span>
              <i class="menu-arrow"></i>
            </a>
            <div class="collapse" :class="{ show: expandedIds.has(cat.id) }">
              <ul class="nav flex-column sub-menu">
                <li
                  v-for="item in cat.filteredItems"
                  :key="item.id"
                  class="nav-item"
                >
                  <router-link
                    class="nav-link"
                    :to="`/categories/${cat.id}/items/${item.id}`"
                    :class="{ active: cat.id === activeCategoryId && item.id === activeItemId }"
                    @click="requestCloseSidebar"
                  >
                    <span class="text-truncate">{{ item.name }}</span>
                  </router-link>
                </li>
                <li v-if="cat.filteredItems.length === 0" class="nav-item">
                  <span class="nav-link" style="opacity: 0.5; font-style: italic;">{{ noMatchesLabel(contentLanguage) }}</span>
                </li>
              </ul>
            </div>
          </li>

          <li v-if="filteredCategories.length === 0" class="nav-item">
            <span class="nav-link" style="opacity: 0.5; font-style: italic;">{{ noCategoriesMatchLabel(contentLanguage) }}</span>
          </li>
          </template>
        </ul>
      </li>
    </ul>
  </nav>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { sharedCategories } from '../api/cache'
import { configurationServices, configurationServicesLoading, configurationServicesError, serviceConfigurationFiles, serviceConfigurationLoading, serviceConfigurationErrors, ensureConfigurationTree, refreshServiceConfigurations } from '../api/configurationCache'
import { serviceDisplayName } from '../utils/configurationNavigation'
import { useContentLanguage } from '../composables/useContentLanguage'
import { useCurrentUser } from '../composables/useCurrentUser'
import { localizedCategoryLabel, localizedImageLabel, breadcrumbCategoriesLabel, noCategoriesMatchLabel, noMatchesLabel, filterLabel } from '../utils/localizedLabels'

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
const user = useCurrentUser()
const contentLanguage = useContentLanguage()
const filterText = ref('')
const expandedIds = ref(new Set<string>())
const expandedServices = ref(new Set<string>())
const isConfigurationNavigation = computed(() => route.meta.navigation === 'configuration')
const activeServiceName = computed(() => String(route.params.serviceName || ''))
const activeConfigurationName = computed(() => typeof route.query.config === 'string' ? route.query.config : '')

const filteredServices = computed(() => {
  const query = filterText.value.toLowerCase().trim()
  return configurationServices.value.map(id => {
    const name = serviceDisplayName(id)
    const files = serviceConfigurationFiles.value[id] ?? []
    const matchesService = !query || name.toLowerCase().includes(query) || id.includes(query)
    return { id, name, files: matchesService ? files : files.filter(file => file.filename.toLowerCase().includes(query) || file.provider?.toLowerCase().includes(query)), matchesService }
  }).filter(service => service.matchesService || service.files.length)
})

async function loadConfigurationTree(): Promise<void> {
  await ensureConfigurationTree().catch(() => undefined)
}

async function retryService(service: string): Promise<void> {
  await refreshServiceConfigurations(service).catch(() => undefined)
}

watch(isConfigurationNavigation, (active) => {
  filterText.value = ''
  if (active) void loadConfigurationTree()
}, { immediate: true })

watch(activeServiceName, name => {
  if (!name) return
  expandedServices.value = new Set([...expandedServices.value, name])
}, { immediate: true })

const activeCategoryId = computed(() => route.params.categoryId as string || '')
const activeItemId = computed(() => route.params.itemId as string || '')

interface FilteredCategory {
  id: string
  name: string
  filteredItems: { id: string; name: string }[]
}

const filteredCategories = computed((): FilteredCategory[] => {
  const lang = contentLanguage.value
  const q = filterText.value.toLowerCase().trim()
  const categories = sharedCategories.value
  if (!q) {
    return categories.map((c) => ({
      id: c.id,
      name: localizedCategoryLabel(c, lang),
      filteredItems: c.items.map((i) => ({
        id: i.id,
        name: localizedImageLabel(i, lang),
      })),
    }))
  }
  const result: FilteredCategory[] = []
  for (const cat of categories) {
    const catLabel = localizedCategoryLabel(cat, lang)
    const catMatch = catLabel.toLowerCase().includes(q)
    const matchedItems = cat.items.filter((i) =>
      localizedImageLabel(i, lang).toLowerCase().includes(q),
    )
    if (catMatch || matchedItems.length > 0) {
      result.push({
        id: cat.id,
        name: catLabel,
        filteredItems: catMatch
          ? cat.items.map((i) => ({ id: i.id, name: localizedImageLabel(i, lang) }))
          : matchedItems.map((i) => ({ id: i.id, name: localizedImageLabel(i, lang) })),
      })
    }
  }
  return result
})

watch(filterText, (q) => {
  if (q.trim()) {
    if (isConfigurationNavigation.value) {
      expandedServices.value = new Set([...expandedServices.value, ...filteredServices.value.map(service => service.id)])
      return
    }
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

const expandAllNodes = (): void => {
  if (isConfigurationNavigation.value) expandedServices.value = new Set(configurationServices.value)
  else expandedIds.value = new Set(sharedCategories.value.map((c) => c.id))
}

const collapseAllNodes = (): void => {
  if (isConfigurationNavigation.value) expandedServices.value = new Set()
  else expandedIds.value = new Set()
}

const toggleService = (service: string): void => {
  const next = new Set(expandedServices.value)
  if (next.has(service)) next.delete(service)
  else next.add(service)
  expandedServices.value = next
  requestCloseSidebar()
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
nav.sidebar.sidebar-offcanvas {
  display: flex;
  flex-direction: column;
}

nav.sidebar.sidebar-offcanvas > .nav {
  height: 100%;
  min-height: 0;
  flex: 1 1 auto;
  overflow: hidden;
}

.cd-sidebar-tree {
  min-height: 0;
  flex: 1 1 auto;
  overflow: hidden;
}

.cd-sidebar-tree-list {
  height: 100%;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  padding-bottom: 3.5rem;
  flex-wrap: nowrap;
  flex-direction: column;
}

.cd-sidebar-tree-list > .nav-item.active > .nav-link {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 7px;
  margin: 0 24px;
  padding-left: 30px;
  padding-right: 18px;
  width: calc(100% - 48px);
}

nav.sidebar.sidebar-offcanvas > .nav .cd-sidebar-tree-list .collapse .sub-menu .nav-item .nav-link {
  transition: none;
}

nav.sidebar.sidebar-offcanvas > .nav .cd-sidebar-tree-list .collapse .sub-menu .nav-item .nav-link.active {
  background: rgba(255, 255, 255, 0.29);
  border-radius: 6px;
  color: #fff;
  font-weight: 600;
  margin-left: 54px;
  margin-right: 24px;
  padding: 8px 15px;
}

nav.sidebar.sidebar-offcanvas > .nav .cd-sidebar-tree-list .collapse .sub-menu .nav-item .nav-link.active::before {
  left: -19px;
}

.cd-sidebar-tree-list .sub-menu .nav-link .text-truncate {
  min-width: 0;
}

.cd-sidebar-root-link { color: inherit; }
.cd-sidebar-root-link:hover { color: #fff; text-decoration: none; }
.cd-sidebar-status { opacity: .65; font-style: italic; white-space: normal; }
.cd-sidebar-retry { color: #fff; background: transparent; border: 0; text-align: left; }

.cd-sidebar-tree-list > .nav-item > .nav-link .menu-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Placeholder defaults are dim; sidebar field needs high contrast on blue. */
.cd-sidebar-filter-input::placeholder {
  color: rgba(255, 255, 255, 0.9);
  opacity: 1;
}

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
