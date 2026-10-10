<template>
  <nav class="navbar default-layout col-lg-12 col-12 p-0 fixed-top d-flex flex-row">
    <div
      class="text-center navbar-brand-wrapper d-flex align-items-top justify-content-center"
      :class="{ 'cd-brand-sidebar-trigger': isCompactNav }"
      :aria-controls="isCompactNav ? 'sidebar' : undefined"
      :aria-label="isCompactNav ? 'Open or close navigation menu' : undefined"
      :aria-expanded="isCompactNav ? sidebarActive : undefined"
      @click.capture="onBrandAreaClick"
    >
      <router-link class="navbar-brand brand-logo" to="/categories">
        <img :src="logoUrl" alt="KiddoWords" />
      </router-link>
      <router-link class="navbar-brand brand-logo-mini" to="/categories">
        <img :src="logoMiniUrl" alt="KiddoWords" />
      </router-link>
    </div>
    <div class="navbar-menu-wrapper d-flex align-items-center flex-grow-1 justify-content-end">
      <MockDataBadge v-if="areBackendRequestsMocked" class="navbar-mock-badge" />
      <ul class="navbar-nav flex-row align-items-center">
        <li ref="categoriesDropdown" class="nav-item dropdown categories-dropdown" @keydown.esc.stop.prevent="closeCategoriesDropdown">
          <button
            ref="categoriesToggle"
            type="button"
            class="nav-link dropdown-toggle px-2 d-flex align-items-center categories-nav-toggle"
            :aria-label="breadcrumbCategoriesLabel(selectedLang)"
            :title="breadcrumbCategoriesLabel(selectedLang)"
            aria-controls="categories-nav-menu"
            :aria-expanded="categoriesOpen"
            @click="toggleCategories"
          >
            <div class="d-inline-flex config-nav-icon-wrap">
              <CategoryListIcon class="categories-nav-icon" />
            </div>
            <span class="profile-text font-weight-medium d-none d-md-block">{{ breadcrumbCategoriesLabel(selectedLang) }}</span>
          </button>
          <div id="categories-nav-menu" class="dropdown-menu dropdown-menu-right navbar-dropdown py-2" :class="{ show: categoriesOpen }">
            <RouterLink class="dropdown-item" :to="{ name: 'dashboard' }" @click="categoriesOpen = false">
              <CategoryListIcon class="categories-item-icon" />
              {{ allCategoriesLabel(selectedLang) }}
            </RouterLink>
            <div class="dropdown-divider"></div>
            <span v-if="categoriesLoading && !categories.length" class="dropdown-item cfg-menu-status text-muted" role="status">
              {{ loadingCategoriesLabel(selectedLang) }}
            </span>
            <span v-else-if="categoriesError && !categories.length" class="dropdown-item cfg-menu-status text-danger" role="alert">
              {{ categoriesLoadErrorLabel(selectedLang, categoriesUnavailable) }}
            </span>
            <template v-else>
              <RouterLink
                v-for="category in categories"
                :key="category.id"
                class="dropdown-item"
                :to="{ name: 'category', params: { categoryId: category.id } }"
                :title="localizedCategoryLabel(category, selectedLang)"
                @click="categoriesOpen = false"
              >
                <CategoryListIcon class="categories-item-icon" />
                <span class="text-truncate">{{ localizedCategoryLabel(category, selectedLang) }}</span>
              </RouterLink>
              <span v-if="!categories.length" class="dropdown-item cfg-menu-status text-muted" role="status">
                {{ noCategoriesFoundLabel(selectedLang) }}
              </span>
            </template>
          </div>
        </li>
        <li v-if="canEdit" ref="configDropdown" class="nav-item dropdown config-dropdown">
          <a
            class="nav-link dropdown-toggle px-2 d-flex align-items-center"
            href="#"
            :aria-expanded="configOpen"
            @click.prevent="toggleConfig"
          >
            <div class="d-inline-flex config-nav-icon-wrap">
              <i class="mdi mdi-settings cfg-nav-icon"></i>
            </div>
            <span class="profile-text font-weight-medium d-none d-md-block">{{ configurationLabel(selectedLang) }}</span>
          </a>
          <div class="dropdown-menu dropdown-menu-right navbar-dropdown py-2" :class="{ show: configOpen }">
            <RouterLink class="dropdown-item" :to="{ name: 'configuration' }" @click="configOpen = false">
              <i class="mdi mdi-view-grid-outline cfg-item-icon" aria-hidden="true"></i>
              {{ configurationText.allServices }}
            </RouterLink>
            <div class="dropdown-divider"></div>
            <span v-if="configServicesLoading" class="dropdown-item cfg-menu-status text-muted">
              {{ loadingServicesLabel(selectedLang) }}
            </span>
            <span v-else-if="configServicesError" class="dropdown-item cfg-menu-status text-danger">
              {{ failedToLoadServicesLabel(selectedLang) }}
            </span>
            <template v-else>
              <a
                v-for="svc in configServices"
                :key="svc"
                class="dropdown-item"
                href="#"
                @click.prevent="selectConfigService(svc)"
              >
                <ServiceIcon :service="svc" class="cfg-item-icon" />
                {{ serviceLabel(svc) }}
              </a>
            </template>
          </div>
        </li>
        <li ref="languageDropdown" class="nav-item dropdown language-dropdown">
          <a
            class="nav-link dropdown-toggle px-2 d-flex align-items-center"
            href="#"
            @click.prevent="toggleLang"
          >
            <div class="d-inline-flex mr-0 mr-md-3">
              <div class="flag-icon-holder">
                <i :class="'flag-icon flag-icon-' + langFlags[selectedLang]"></i>
              </div>
            </div>
            <span class="profile-text font-weight-medium d-none d-md-block">{{ langLabels[selectedLang] }}</span>
          </a>
          <div class="dropdown-menu dropdown-menu-right navbar-dropdown py-2" :class="{ show: langOpen }">
            <a
              v-for="lang in languages"
              :key="lang"
              class="dropdown-item"
              href="#"
              @click.prevent="selectedLang = lang; langOpen = false"
            >
              <div class="flag-icon-holder">
                <i :class="'flag-icon flag-icon-' + langFlags[lang]"></i>
              </div>
              {{ langLabels[lang] }}
            </a>
          </div>
        </li>
        <li ref="userDropdown" class="nav-item dropdown user-dropdown">
          <a class="nav-link dropdown-toggle" href="#" @click.prevent="toggleUser">
            <img class="img-xs rounded-circle" :src="user.avatarUri" alt="Profile image" />
          </a>
          <div class="dropdown-menu dropdown-menu-right navbar-dropdown" :class="{ show: userOpen }">
            <div class="dropdown-header text-center">
              <img class="img-md rounded-circle" :src="user.avatarUri" alt="Profile image" />
              <p class="mb-1 mt-3 font-weight-semibold">{{ user.name }}</p>
              <p class="font-weight-light text-muted mb-0">{{ user.role }}</p>
            </div>
            <a class="dropdown-item" href="#" @click.prevent="logout">
              Sign Out<i class="dropdown-item-icon mdi mdi-power"></i>
            </a>
          </div>
        </li>
      </ul>
    </div>
    <ConfigurationModal
      v-if="configService"
      :service="configService"
      @close="configService = null"
      @saved="onConfigSaved"
      @configure="openConfigView"
    />
    <Teleport to="body">
      <div v-if="navToast" class="cd-toast cd-toast-success">
        <i class="mdi mdi-check-circle"></i>
        <span>{{ navToast }}</span>
      </div>
    </Teleport>
  </nav>
</template>

<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CategoryListIcon from '../components/CategoryListIcon.vue'
import ConfigurationModal from '../components/ConfigurationModal.vue'
import ServiceIcon from '../components/ServiceIcon.vue'
import MockDataBadge from '../components/MockDataBadge.vue'
import { ensureConfigurationServices, refreshServiceConfigurations } from '../api/configurationCache'
import {
  ensureCategoriesLoaded,
  sharedCategories as categories,
  sharedLoading as categoriesLoading,
  sharedError as categoriesError,
  sharedContentUnavailable as categoriesUnavailable,
} from '../api/cache'
import type { Language } from '../types/viewModels'
import { useContentLanguage } from '../composables/useContentLanguage'
import { useCurrentUser } from '../composables/useCurrentUser'
import { hasWriteAccess } from '../utils/permissions'
import {
  allCategoriesLabel,
  breadcrumbCategoriesLabel,
  categoriesLoadErrorLabel,
  configurationLabel,
  failedToLoadServicesLabel,
  loadingCategoriesLabel,
  loadingServicesLabel,
  localizedCategoryLabel,
  noCategoriesFoundLabel,
} from '../utils/localizedLabels'
import { configurationLabels, localizedServiceLabel } from '../utils/localizedConfigurations'
import logoUrl from '../assets/images/logo.svg'
import logoMiniUrl from '../assets/images/logo-mini.svg'

const props = withDefaults(
  defineProps<{
    /** When the off-canvas sidebar is open (mobile), header dropdowns must close. */
    sidebarActive?: boolean
  }>(),
  { sidebarActive: false },
)

/** Matches Star Admin off-canvas breakpoint (see demo_1/style.css `.sidebar-offcanvas`). */
const COMPACT_NAV_MQ = '(max-width: 991px)'
const isCompactNav = ref(false)
let compactNavMql: MediaQueryList | null = null

const syncCompactNav = (): void => {
  if (compactNavMql) isCompactNav.value = compactNavMql.matches
}

const onBrandAreaClick = (e: MouseEvent): void => {
  if (!isCompactNav.value) return
  e.preventDefault()
  e.stopPropagation()
  emit('toggle-sidebar')
}

const emit = defineEmits<{
  'toggle-sidebar': []
  'close-sidebar': []
}>()

const route = useRoute()
const router = useRouter()
const user = useCurrentUser()
// Configurations are admin-only, so viewers get no Configuration menu.
const canEdit = hasWriteAccess()
const areBackendRequestsMocked = import.meta.env.VITE_MOCK_DATA === 'true'

const languages: Language[] = ['en', 'es', 'fr', 'bg']
const langFlags: Record<Language, string> = { en: 'us', es: 'es', fr: 'fr', bg: 'bg' }
const langLabels: Record<Language, string> = { en: 'English', es: 'Español', fr: 'Français', bg: 'Български' }
const selectedLang = useContentLanguage()
const configurationText = computed(() => configurationLabels(selectedLang.value))
const langOpen = ref(false)
const userOpen = ref(false)

const categoriesOpen = ref(false)
const categoriesDropdown = ref<HTMLElement | null>(null)
const categoriesToggle = ref<HTMLButtonElement | null>(null)
const configOpen = ref(false)
const configDropdown = ref<HTMLElement | null>(null)
const languageDropdown = ref<HTMLElement | null>(null)
const userDropdown = ref<HTMLElement | null>(null)
const configServices = ref<string[]>([])
const configServicesLoading = ref(false)
const configServicesError = ref(false)
const configService = ref<string | null>(null)
const navToast = ref<string | null>(null)
let navToastTimer: ReturnType<typeof setTimeout> | null = null

const closeDropdownsOnOutsideClick = (event: PointerEvent): void => {
  if (!(event.target instanceof Node)) return
  if (categoriesOpen.value && !categoriesDropdown.value?.contains(event.target)) categoriesOpen.value = false
  if (configOpen.value && !configDropdown.value?.contains(event.target)) configOpen.value = false
  if (langOpen.value && !languageDropdown.value?.contains(event.target)) langOpen.value = false
  if (userOpen.value && !userDropdown.value?.contains(event.target)) userOpen.value = false
}

watch(
  () => props.sidebarActive,
  (active) => {
    if (active) {
      categoriesOpen.value = false
      langOpen.value = false
      userOpen.value = false
      configOpen.value = false
    }
  },
)

watch(() => route.fullPath, () => {
  categoriesOpen.value = false
})

onMounted(() => {
  compactNavMql = window.matchMedia(COMPACT_NAV_MQ)
  syncCompactNav()
  compactNavMql.addEventListener('change', syncCompactNav)
  document.addEventListener('pointerdown', closeDropdownsOnOutsideClick)
})

onUnmounted(() => {
  compactNavMql?.removeEventListener('change', syncCompactNav)
  document.removeEventListener('pointerdown', closeDropdownsOnOutsideClick)
  if (navToastTimer) clearTimeout(navToastTimer)
})

const toggleLang = (): void => {
  const next = !langOpen.value
  if (next) {
    categoriesOpen.value = false
    userOpen.value = false
    configOpen.value = false
    emit('close-sidebar')
  }
  langOpen.value = next
}

const toggleUser = (): void => {
  const next = !userOpen.value
  if (next) {
    categoriesOpen.value = false
    langOpen.value = false
    configOpen.value = false
    emit('close-sidebar')
  }
  userOpen.value = next
}

const toggleCategories = (): void => {
  const next = !categoriesOpen.value
  if (next) {
    langOpen.value = false
    userOpen.value = false
    configOpen.value = false
    emit('close-sidebar')
    void ensureCategoriesLoaded().catch(() => undefined)
  }
  categoriesOpen.value = next
}

const closeCategoriesDropdown = (): void => {
  categoriesOpen.value = false
  categoriesToggle.value?.focus()
}

const serviceLabel = (service: string): string =>
  localizedServiceLabel(service, selectedLang.value)

const loadConfigServices = async (): Promise<void> => {
  configServicesLoading.value = true
  configServicesError.value = false
  try {
    configServices.value = await ensureConfigurationServices()
  } catch (err) {
    configServicesError.value = true
  } finally {
    configServicesLoading.value = false
  }
}

const toggleConfig = (): void => {
  const next = !configOpen.value
  if (next) {
    categoriesOpen.value = false
    langOpen.value = false
    userOpen.value = false
    emit('close-sidebar')
    if (!configServices.value.length && !configServicesLoading.value) {
      loadConfigServices()
    }
  }
  configOpen.value = next
}

const selectConfigService = (service: string): void => {
  configOpen.value = false
  configService.value = service
}

const onConfigSaved = (message: string): void => {
  if (configService.value) void refreshServiceConfigurations(configService.value).catch(() => undefined)
  configService.value = null
  if (navToastTimer) clearTimeout(navToastTimer)
  navToast.value = message
  navToastTimer = setTimeout(() => { navToast.value = null }, 4000)
}

const openConfigView = (): void => {
  const service = configService.value
  if (!service) return
  configService.value = null
  router.push({ name: 'service-configuration', params: { serviceName: service } })
}

const logout = (): void => {
  localStorage.removeItem('token')
  router.push('/login')
}
</script>

<style scoped>
.navbar-brand-wrapper .brand-logo > img {
  padding-top: 12px;
}

.cd-brand-sidebar-trigger {
  cursor: pointer;
}

.navbar-mock-badge {
  flex-shrink: 0;
  margin-right: 0.75rem;
}

@media (max-width: 767px) {
  .navbar-mock-badge {
    padding: 0 6px;
    margin-right: 0.25rem;
  }
  .navbar-mock-badge :deep(span:last-child) {
    display: none;
  }
  nav.navbar.default-layout .navbar-menu-wrapper .navbar-nav .nav-item {
    margin-left: 0.25rem;
  }
  nav.navbar.default-layout .navbar-menu-wrapper .navbar-nav .nav-item .nav-link {
    margin-left: 0;
    margin-right: 0;
  }
}

/*
  Star Admin: `.navbar-dropdown { min-width: 100% }` and, below 992px,
  `.navbar-nav .nav-item.dropdown { position: unset }` — dropdowns then size to the full
  menu wrapper. Bootstrap also sets `.navbar-nav .dropdown-menu { position: static }` unless
  a parent `.navbar-expand-*` applies. Pin each trigger + force absolute menus with fixed widths.
*/
.navbar-menu-wrapper .categories-dropdown,
.navbar-menu-wrapper .config-dropdown,
.navbar-menu-wrapper .language-dropdown,
.navbar-menu-wrapper .user-dropdown {
  position: relative !important;
}

nav.navbar.default-layout .navbar-menu-wrapper .navbar-nav .nav-item.categories-dropdown .dropdown-menu.navbar-dropdown,
nav.navbar.default-layout .navbar-menu-wrapper .navbar-nav .nav-item.config-dropdown .dropdown-menu.navbar-dropdown {
  position: absolute !important;
  top: 100% !important;
  left: auto !important;
  right: -8px !important;
  min-width: 210px !important;
  width: 210px !important;
  max-width: calc(100vw - 1rem);
  transform: none !important;
}

@media (max-width: 767px) {
  nav.navbar.default-layout .navbar-menu-wrapper .navbar-nav .nav-item.categories-dropdown .dropdown-menu.navbar-dropdown {
    left: 0 !important;
    right: auto !important;
  }
}

.categories-nav-toggle {
  background: transparent;
  border: 0;
  cursor: pointer;
}

.categories-nav-icon {
  font-size: 1.25rem;
}

.categories-item-icon {
  margin-right: 0.5rem;
  font-size: 1.125rem;
}

.categories-dropdown .dropdown-menu {
  max-height: min(420px, calc(100dvh - 80px));
  overflow-y: auto;
}

.cfg-nav-icon {
  font-size: 1.25rem;
  line-height: 1;
}

.config-nav-icon-wrap {
  margin-right: 0.35rem;
}

.cfg-item-icon {
  margin-right: 0.5rem;
}

.service-icon.cfg-item-icon {
  font-size: 1.125rem;
}

.cfg-menu-status {
  cursor: default;
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1.25;
  font-size: 0.78rem;
}

/* Match Star Admin selector strength so `left: -15px !important` from the theme does not win. */
nav.navbar.default-layout .navbar-menu-wrapper .navbar-nav .nav-item.language-dropdown .dropdown-menu.navbar-dropdown {
  position: absolute !important;
  top: 100% !important;
  left: auto !important;
  right: -8px !important;
  min-width: 170px !important;
  width: 170px !important;
  max-width: calc(100vw - 1rem);
  transform: none !important;
}

nav.navbar.default-layout .navbar-menu-wrapper .navbar-nav .nav-item.user-dropdown .dropdown-menu.navbar-dropdown {
  position: absolute !important;
  top: 100% !important;
  left: auto !important;
  right: 0 !important;
  min-width: 250px !important;
  width: auto !important;
  max-width: min(320px, calc(100vw - 1rem));
  transform: none !important;
}

/* Configuration saved toast (mirrors the cd-toast pattern) */
.cd-toast {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  z-index: 99999;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  max-width: 360px;
  padding: 0.75rem 1rem;
  border-radius: 6px;
  color: #fff;
  font-size: 0.9rem;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
}
.cd-toast i {
  font-size: 1.2rem;
  flex-shrink: 0;
}
.cd-toast-success {
  background: #16a34a;
}
</style>
