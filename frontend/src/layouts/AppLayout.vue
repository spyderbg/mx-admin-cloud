<template>
  <div class="container-scroller">
    <AppNavbar @toggle-sidebar="toggleSidebar" />
    <div class="container-fluid page-body-wrapper">
      <AppSidebar :is-active="isSidebarActive" />
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
import { ref } from 'vue'
import AppNavbar from './AppNavbar.vue'
import AppSidebar from './AppSidebar.vue'
import AppFooter from './AppFooter.vue'

const isSidebarActive = ref(false)

const toggleSidebar = (): void => {
  isSidebarActive.value = !isSidebarActive.value
}
</script>
