<template>
  <div class="auto-form-wrapper">
    <form @submit.prevent="handleLogin">
      <div class="form-group">
        <label class="label">Username</label>
        <div class="input-group">
          <input
            type="text"
            class="form-control"
            placeholder="Username"
            v-model="username"
            required
          />
          <div class="input-group-append">
            <span class="input-group-text">
              <span class="input-icon" aria-hidden="true">✓</span>
            </span>
          </div>
        </div>
      </div>
      <div class="form-group">
        <label class="label">Password</label>
        <div class="input-group">
          <input
            type="password"
            class="form-control"
            placeholder="*********"
            v-model="password"
            required
          />
          <div class="input-group-append">
            <span class="input-group-text">
              <span class="input-icon" aria-hidden="true">✓</span>
            </span>
          </div>
        </div>
      </div>
      <p v-if="error" class="error-msg">{{ error }}</p>
      <div class="form-group">
        <button type="submit" class="btn btn-primary submit-btn btn-block">
          Login
        </button>
      </div>
      <div class="form-group d-flex justify-content-between">
        <div class="form-check form-check-flat mt-0">
          <label class="form-check-label">
            <input type="checkbox" class="form-check-input" v-model="keepSignedIn" />
            Keep me signed in
          </label>
        </div>
        <a href="#" class="text-small forgot-password text-black">Forgot Password</a>
      </div>
      <div class="form-group">
        <button type="button" class="btn btn-block g-login" disabled title="Not configured">
          <img class="mr-3" :src="iconGoogle" alt="" width="18" height="19" />
          Log in with Google
        </button>
      </div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { AxiosError } from 'axios'
import { login } from '../api/auth'
import iconGoogle from '../assets/images/file-icons/icon-google.svg'

const username = ref<string>('')
const password = ref<string>('')
const error = ref<string>('')
const keepSignedIn = ref<boolean>(true)
const router = useRouter()

const handleLogin = async (): Promise<void> => {
  error.value = ''
  try {
    await login(username.value, password.value)
    router.push('/')
  } catch (err: unknown) {
    const axiosErr = err as AxiosError<{ detail?: string }>
    error.value = axiosErr.response?.data?.detail || 'Invalid credentials'
  }
}
</script>

<style scoped>
.input-icon {
  font-size: 0.9rem;
  color: #9ca3af;
}
</style>
