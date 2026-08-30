<template>
  <div class="container-scroller">
    <AppNavbar
      :sidebar-active="isSidebarActive"
      @toggle-sidebar="toggleSidebar"
      @close-sidebar="closeSidebar"
    />
    <div class="container-fluid page-body-wrapper mx-page-body">
      <AppSidebar :is-active="isSidebarActive" @close-sidebar="closeSidebar" />
      <div class="main-panel">
        <div class="content-wrapper">
          <router-view />
        </div>
        <AppFooter />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, provide } from 'vue'
import AppNavbar from './AppNavbar.vue'
import AppSidebar from './AppSidebar.vue'
import AppFooter from './AppFooter.vue'
import { createContentLanguageRef, contentLanguageKey } from '../composables/useContentLanguage'

const isSidebarActive = ref(false)

const contentLanguage = createContentLanguageRef()
provide(contentLanguageKey, contentLanguage)

const toggleSidebar = (): void => {
  isSidebarActive.value = !isSidebarActive.value
}

const closeSidebar = (): void => {
  isSidebarActive.value = false
}
</script>

<style scoped>
@media (min-width: 992px) {
  .mx-page-body :deep(.sidebar) {
    position: fixed;
    top: 63px;
    bottom: 0;
    left: 0;
    height: calc(100vh - 63px);
    min-height: 0;
    overflow-x: hidden;
    overflow-y: hidden;
  }

  .mx-page-body .main-panel {
    margin-left: 270px;
  }
}
</style>
