<template>
  <nav class="navbar default-layout col-lg-12 col-12 p-0 fixed-top d-flex flex-row">
    <div class="text-center navbar-brand-wrapper d-flex align-items-top justify-content-center">
      <router-link class="navbar-brand brand-logo" to="/categories">
        <img :src="logoUrl" alt="KiddoWords" />
      </router-link>
      <router-link class="navbar-brand brand-logo-mini" to="/categories">
        <img :src="logoMiniUrl" alt="KiddoWords" />
      </router-link>
    </div>
    <div class="navbar-menu-wrapper d-flex align-items-center">
      <ul class="navbar-nav">
        <li class="nav-item dropdown language-dropdown">
          <a
            class="nav-link dropdown-toggle px-2 d-flex align-items-center"
            href="#"
            @click.prevent="langOpen = !langOpen"
          >
            <div class="d-inline-flex mr-0 mr-md-3">
              <div class="flag-icon-holder">
                <i :class="'flag-icon flag-icon-' + langFlags[selectedLang]"></i>
              </div>
            </div>
            <span class="profile-text font-weight-medium d-none d-md-block">{{ langLabels[selectedLang] }}</span>
          </a>
          <div class="dropdown-menu dropdown-menu-left navbar-dropdown py-2" :class="{ show: langOpen }">
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
      </ul>
      <ul class="navbar-nav ml-auto">
        <li class="nav-item dropdown d-none d-xl-inline-block user-dropdown">
          <a class="nav-link dropdown-toggle" href="#" @click.prevent="userOpen = !userOpen">
            <img class="img-xs rounded-circle" :src="user.avatarUrl" alt="Profile image" />
          </a>
          <div class="dropdown-menu dropdown-menu-right navbar-dropdown" :class="{ show: userOpen }">
            <div class="dropdown-header text-center">
              <img class="img-md rounded-circle" :src="user.avatarUrl" alt="Profile image" />
              <p class="mb-1 mt-3 font-weight-semibold">{{ user.name }}</p>
              <p class="font-weight-light text-muted mb-0">{{ user.role }}</p>
            </div>
            <a class="dropdown-item" href="#" @click.prevent="logout">
              Sign Out<i class="dropdown-item-icon mdi mdi-power"></i>
            </a>
          </div>
        </li>
      </ul>
      <button
        class="navbar-toggler navbar-toggler-right d-lg-none align-self-center"
        type="button"
        @click="$emit('toggle-sidebar')"
      >
        <span class="mdi mdi-menu"></span>
      </button>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { mockUser } from '../data/mockData'
import type { Language } from '../types'
import logoUrl from '../assets/images/logo.svg'
import logoMiniUrl from '../assets/images/logo-mini.svg'

defineEmits(['toggle-sidebar'])

const router = useRouter()
const user = mockUser

const languages: Language[] = ['en', 'es', 'fr', 'bg']
const langFlags: Record<Language, string> = { en: 'us', es: 'es', fr: 'fr', bg: 'bg' }
const langLabels: Record<Language, string> = { en: 'English', es: 'Español', fr: 'Français', bg: 'Български' }
const selectedLang = ref<Language>('en')
const langOpen = ref(false)
const userOpen = ref(false)

const logout = (): void => {
  localStorage.removeItem('token')
  router.push('/login')
}
</script>

<style scoped>
.navbar-brand-wrapper .brand-logo > img {
  padding-top: 12px;
}
</style>
