import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import sirv from 'sirv'
import path from 'node:path'

const contentDir = path.resolve(__dirname, '../content')

export default defineConfig(({ mode }) => {
  loadEnv(mode, process.cwd(), '')
  const backendTarget = 'http://127.0.0.1:8000'

  console.log('backendTarget', backendTarget)

  return {
    plugins: [
      vue(),
      {
        name: 'serve-content',
        configureServer(server) {
          server.middlewares.use('/content', sirv(contentDir, { dev: true, etag: true }))
        },
      },
    ],
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: {
        '/api': {
          target: backendTarget,
          changeOrigin: true,
        },
      }
    }
  }
})
