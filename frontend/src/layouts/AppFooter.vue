<template>
  <footer class="footer">
    <div class="container-fluid d-flex justify-content-between align-items-center">
      <span class="text-muted">Kiddo Words <br class="d-md-none">Content Management <span class="text-nowrap">v{{ version }}</span></span>
      <span class="text-muted text-right">
        <a href="https://marxmanstudio.com/" target="_blank" rel="noopener noreferrer" class="text-muted">
          &copy; 2025 MarXman Studio. <br class="d-md-none">All rights reserved.
        </a>
      </span>
    </div>
  </footer>
</template>

<script setup lang="ts">
import { version } from '../../package.json'
</script>

<style scoped>
@media (min-width: 768px) {
  .footer {
    padding-top: 1rem;
    padding-bottom: 1rem;
  }
}
</style>
