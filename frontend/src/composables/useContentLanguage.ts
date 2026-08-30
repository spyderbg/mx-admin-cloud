import { ref, inject, watch, type InjectionKey, type Ref } from 'vue'
import type { Language } from '../types/viewModels'

const STORAGE_KEY = 'mx-admin-content-language'

export const contentLanguageKey: InjectionKey<Ref<Language>> = Symbol('contentLanguage')

function readStoredLanguage(): Language {
  const v = localStorage.getItem(STORAGE_KEY)
  if (v === 'en' || v === 'es' || v === 'fr' || v === 'bg') return v
  return 'en'
}

/** Call once in AppLayout; returned ref is provided to descendants. */
export function createContentLanguageRef(): Ref<Language> {
  const contentLanguage = ref<Language>(readStoredLanguage())
  watch(contentLanguage, (l) => {
    localStorage.setItem(STORAGE_KEY, l)
  })
  return contentLanguage
}

export function useContentLanguage(): Ref<Language> {
  const injected = inject(contentLanguageKey)
  if (!injected) {
    throw new Error('useContentLanguage() must be used within AppLayout')
  }
  return injected
}
