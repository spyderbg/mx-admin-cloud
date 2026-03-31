import { createApp } from 'vue'
import './assets/vendors/iconfonts/mdi/css/materialdesignicons.min.css'
import './assets/vendors/iconfonts/flag-icon-css/css/flag-icon.min.css'
import './assets/vendors/font-awesome/css/font-awesome.min.css'
import './assets/vendors/css/vendor.bundle.base.css'
import './assets/vendors/css/vendor.bundle.addons.css'
import './assets/css/shared/style.css'
import './assets/css/demo_1/style.css'
import './assets/css/auth.css'
import App from './App.vue'
import router from './router'

async function main() {
  if (import.meta.env.VITE_DEBUG_MODE === 'true') {
    const { worker } = await import('./mocks/browser')
    await worker.start({ onUnhandledRequest: 'bypass' })
    if (!localStorage.getItem('token')) {
      localStorage.setItem('token', 'debug-token')
    }
  }

  createApp(App).use(router).mount('#kiddo_app')
}

main()
