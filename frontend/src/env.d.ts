/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ARCHIVE_CONFIRMATION_MODE?: 'complex' | 'simple'
  readonly VITE_MOCK_DATA: string
  readonly VITE_VERBOSE_REQUESTS: string
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, never>, Record<string, never>, unknown>
  export default component
}

declare module '*.svg' {
  const src: string
  export default src
}
