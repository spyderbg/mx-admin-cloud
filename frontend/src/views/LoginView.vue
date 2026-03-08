<template>
  <div class="container-fluid page-body-wrapper full-page-wrapper auth-page">
    <div class="content-wrapper d-flex align-items-center auth auth-bg-1 theme-one">
      <div class="row w-100">
        <div class="col-lg-4 mx-auto">
          <LoginForm />
          <ul class="auth-footer">
            <li><a href="#">Conditions</a></li>
            <li><a href="#">Help</a></li>
            <li><a href="#">Terms</a></li>
          </ul>
          <p class="footer-text text-center">KiddoWords Content Management</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import LoginForm from '../components/LoginForm.vue'
</script>
