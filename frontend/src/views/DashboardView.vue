<template>
  <div class="dashboard">
    <div class="page-header">
      <h3 class="page-title">{{ pageTitle }}</h3>
    </div>

    <div v-if="loading" class="text-center py-5">
      <div class="spinner-border text-primary" role="status"></div>
      <p class="mt-2 text-muted">Loading categories...</p>
    </div>

    <p v-else-if="error" class="text-danger text-center py-5">{{ error }}</p>

    <div v-if="cards.length === 0" class="text-center py-5">
      <p class="text-muted">No categories found.</p>
    </div>

    <div v-else class="row">
      <div
        v-for="card in cards"
        :key="card.id"
        class="col-md-6 col-lg-4 col-xl-3 mb-4"
      >
        <div
          class="cd-card card-hover"
          style="border: 1px solid rgba(0,0,0,0.12); border-radius: 12px; overflow: hidden; cursor: pointer; transition: box-shadow 0.2s, transform 0.2s;"
          @mouseover="hoverCard(card.id, true)"
          @mouseleave="hoverCard(card.id, false)"
          @click="goToCategory(card.id)"
        >
          <div
            class="d-flex align-items-center p-4"
            :class="hoveredCard === card.id ? 'bg-light' : ''"
          >
            <img
              :src="card.iconUri"
              :alt="card.name"
              class="cd-card-icon mr-3"
              style="width: 64px; height: 64px; border-radius: 10px; object-fit: cover; flex-shrink: 0;"
            />
            <div class="flex-grow-1">
              <h5 class="mb-1 text-truncate" :title="card.name">{{ card.name }}</h5>
              <p class="text-muted mb-0" style="font-size: 0.875rem;">
                {{ itemCountLabel(contentLanguage, card.itemCount) }}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useContentLanguage } from '../composables/useContentLanguage'
import { ensureCategoriesLoaded, sharedCategories } from '../api/cache'
import { breadcrumbCategoriesLabel, localizedCategoryLabel, itemCountLabel } from '../utils/localizedLabels'
import { toPascalCaseWords } from '../utils/pascalCaseWords'


const router = useRouter()
const contentLanguage = useContentLanguage()
const loading = ref(true)
const error = ref('')
const hoveredCard = ref('')

const pageTitle = computed(() =>
  toPascalCaseWords(breadcrumbCategoriesLabel(contentLanguage.value)),
)

const hoverCard = (id: string, isHovered: boolean): void => {
  hoveredCard.value = isHovered ? id : ''
}

const goToCategory = (id: string): void => {
  router.push(`/categories/${id}`)
}

const cards = computed(() => {
  const lang = contentLanguage.value
  return sharedCategories.value.map((c) => ({
    id: c.id,
    name: localizedCategoryLabel(c, lang),
    iconUri: c.thumbnail_uri ?? `/content/${localizedCategoryLabel(c, 'en')}/icon.png`,
    itemCount: c.items?.length ?? 0,
  }))
})

onMounted(async () => {
  try {
    await ensureCategoriesLoaded()
  } catch (err) {
    error.value = 'Failed to load categories.'
    console.error(err)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.card-hover {
  background: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.card-hover:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}
</style>
