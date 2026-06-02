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
      <ul class="navbar-nav flex-row align-items-center">
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
  </nav>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { mockUser } from '../data/mockData'
import type { Language } from '../types'
import { useContentLanguage } from '../composables/useContentLanguage'
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
const user = mockUser

const languages: Language[] = ['en', 'es', 'fr', 'bg']
const langFlags: Record<Language, string> = { en: 'us', es: 'es', fr: 'fr', bg: 'bg' }
const langLabels: Record<Language, string> = { en: 'English', es: 'Español', fr: 'Français', bg: 'Български' }
const selectedLang = useContentLanguage()
const langOpen = ref(false)
const userOpen = ref(false)

watch(
  () => props.sidebarActive,
  (active) => {
    if (active) {
      langOpen.value = false
      userOpen.value = false
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
})

const toggleLang = (): void => {
  const next = !langOpen.value
  if (next) {
    userOpen.value = false
    emit('close-sidebar')
  }
  langOpen.value = next
}

const toggleUser = (): void => {
  const next = !userOpen.value
  if (next) {
    langOpen.value = false
    emit('close-sidebar')
  }
  userOpen.value = next
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

/*
  Star Admin: `.navbar-dropdown { min-width: 100% }` and, below 992px,
  `.navbar-nav .nav-item.dropdown { position: unset }` — dropdowns then size to the full
  menu wrapper. Bootstrap also sets `.navbar-nav .dropdown-menu { position: static }` unless
  a parent `.navbar-expand-*` applies. Pin each trigger + force absolute menus with fixed widths.
*/
.navbar-menu-wrapper .language-dropdown,
.navbar-menu-wrapper .user-dropdown {
  position: relative !important;
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
</style>
