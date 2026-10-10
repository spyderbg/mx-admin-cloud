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
      <div v-if="googleClientId" class="form-group">
        <div ref="googleButton" class="google-button" aria-label="Log in with Google"></div>
      </div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { AxiosError } from 'axios'
import { getAuthConfig, login, loginWithGoogle } from '../api/auth'
import { loadGoogleIdentity, type GoogleCredentialResponse } from '../utils/googleIdentity'

const username = ref<string>('')
const password = ref<string>('')
const error = ref<string>('')
const keepSignedIn = ref<boolean>(true)
const googleClientId = ref<string>('')
const googleButton = ref<HTMLElement | null>(null)
const router = useRouter()

const failureMessage = (err: unknown, fallback: string): string => {
  const axiosErr = err as AxiosError<{ detail?: string }>
  return axiosErr.response?.data?.detail || fallback
}

const handleLogin = async (): Promise<void> => {
  error.value = ''
  try {
    await login(username.value, password.value)
    router.push('/')
  } catch (err: unknown) {
    error.value = failureMessage(err, 'Invalid credentials')
  }
}

const handleGoogleCredential = async ({ credential }: GoogleCredentialResponse): Promise<void> => {
  error.value = ''
  try {
    await loginWithGoogle(credential)
    router.push('/')
  } catch (err: unknown) {
    error.value = failureMessage(err, 'Google sign-in failed')
  }
}

// The Google button appears only when the backend allows Google sign-in; password login
// keeps working whether or not the options or Google's script can be loaded.
onMounted(async () => {
  let clientId: string | null | undefined
  try {
    const config = await getAuthConfig()
    clientId = config.google_enabled ? config.google_client_id : null
  } catch {
    return
  }
  if (!clientId) return
  googleClientId.value = clientId
  try {
    const accounts = await loadGoogleIdentity()
    await nextTick()
    if (!googleButton.value) return
    accounts.initialize({ client_id: clientId, callback: handleGoogleCredential, ux_mode: 'popup', context: 'signin' })
    accounts.renderButton(googleButton.value, {
      type: 'standard', theme: 'outline', size: 'large', text: 'signin_with', shape: 'rectangular',
      logo_alignment: 'left', width: Math.max(200, Math.min(400, googleButton.value.clientWidth)),
    })
  } catch {
    googleClientId.value = ''
    error.value = 'Google sign-in could not be loaded. Log in with your username and password.'
  }
})
</script>

<style scoped>
.input-icon {
  font-size: 0.9rem;
  color: #9ca3af;
}

.google-button {
  display: flex;
  justify-content: center;
  min-height: 44px;
}
</style>
