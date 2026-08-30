<template>
  <nav class="navbar default-layout col-lg-12 col-12 p-0 fixed-top d-flex flex-row">
    <div
      class="text-center navbar-brand-wrapper d-flex align-items-top justify-content-center"
      :class="{ 'cd-brand-sidebar-trigger': isCompactNav }"
      :aria-controls="isCompactNav ? 'sidebar' : undefined"
      :aria-label="isCompactNav ? 'Open or close categories menu' : undefined"
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
        <li class="nav-item dropdown config-dropdown">
          <a
            class="nav-link dropdown-toggle px-2 d-flex align-items-center"
            href="#"
            @click.prevent="toggleConfig"
          >
            <div class="d-inline-flex config-nav-icon-wrap">
              <i class="mdi mdi-settings cfg-nav-icon"></i>
            </div>
            <span class="profile-text font-weight-medium d-none d-md-block">{{ configurationLabel(selectedLang) }}</span>
          </a>
          <div class="dropdown-menu dropdown-menu-right navbar-dropdown py-2" :class="{ show: configOpen }">
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
                <i class="mdi mdi-tune cfg-item-icon"></i>
                {{ serviceLabel(svc) }}
              </a>
            </template>
          </div>
        </li>
        <li class="nav-item dropdown language-dropdown">
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
        <li class="nav-item dropdown user-dropdown">
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
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import ConfigurationModal from '../components/ConfigurationModal.vue'
import MockDataBadge from '../components/MockDataBadge.vue'
import { listConfigurationServices } from '../api/configurationClient'
import type { Language } from '../types/viewModels'
import { useContentLanguage } from '../composables/useContentLanguage'
import { useCurrentUser } from '../composables/useCurrentUser'
import { configurationLabel, failedToLoadServicesLabel, loadingServicesLabel } from '../utils/localizedLabels'
import logoUrl from '../assets/images/logo.svg'
import logoMiniUrl from '../assets/images/logo-mini.svg'

const props = withDefaults(
  defineProps<{
    /** When the off-canvas category sidebar is open (mobile), header dropdowns must close. */
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

const router = useRouter()
const user = useCurrentUser()
const areBackendRequestsMocked = import.meta.env.VITE_MOCK_DATA === 'true'

const languages: Language[] = ['en', 'es', 'fr', 'bg']
const langFlags: Record<Language, string> = { en: 'us', es: 'es', fr: 'fr', bg: 'bg' }
const langLabels: Record<Language, string> = { en: 'English', es: 'Español', fr: 'Français', bg: 'Български' }
const selectedLang = useContentLanguage()
const langOpen = ref(false)
const userOpen = ref(false)

const configOpen = ref(false)
const configServices = ref<string[]>([])
const configServicesLoading = ref(false)
const configServicesError = ref(false)
const configService = ref<string | null>(null)
const navToast = ref<string | null>(null)
let navToastTimer: ReturnType<typeof setTimeout> | null = null

watch(
  () => props.sidebarActive,
  (active) => {
    if (active) {
      langOpen.value = false
      userOpen.value = false
      configOpen.value = false
    }
  },
)

onMounted(() => {
  compactNavMql = window.matchMedia(COMPACT_NAV_MQ)
  syncCompactNav()
  compactNavMql.addEventListener('change', syncCompactNav)
})

onUnmounted(() => {
  compactNavMql?.removeEventListener('change', syncCompactNav)
  if (navToastTimer) clearTimeout(navToastTimer)
})

const toggleLang = (): void => {
  const next = !langOpen.value
  if (next) {
    userOpen.value = false
    configOpen.value = false
    emit('close-sidebar')
  }
  langOpen.value = next
}

const toggleUser = (): void => {
  const next = !userOpen.value
  if (next) {
    langOpen.value = false
    configOpen.value = false
    emit('close-sidebar')
  }
  userOpen.value = next
}

const serviceLabel = (service: string): string =>
  service
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')

const loadConfigServices = async (): Promise<void> => {
  configServicesLoading.value = true
  configServicesError.value = false
  try {
    const result = await listConfigurationServices()
    configServices.value = result.services
  } catch (err) {
    configServicesError.value = true
  } finally {
    configServicesLoading.value = false
  }
}

const toggleConfig = (): void => {
  const next = !configOpen.value
  if (next) {
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
  configService.value = null
  if (navToastTimer) clearTimeout(navToastTimer)
  navToast.value = message
  navToastTimer = setTimeout(() => { navToast.value = null }, 4000)
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

/*
  Star Admin: `.navbar-dropdown { min-width: 100% }` and, below 992px,
  `.navbar-nav .nav-item.dropdown { position: unset }` — dropdowns then size to the full
  menu wrapper. Bootstrap also sets `.navbar-nav .dropdown-menu { position: static }` unless
  a parent `.navbar-expand-*` applies. Pin each trigger + force absolute menus with fixed widths.
*/
.navbar-menu-wrapper .config-dropdown,
.navbar-menu-wrapper .language-dropdown,
.navbar-menu-wrapper .user-dropdown {
  position: relative !important;
}

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
