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

    <div v-else-if="cards.length === 0" class="text-center py-5">
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
                {{ card.itemCount }} item{{ card.itemCount !== 1 ? 's' : '' }}
              </p>
            </div>
            <i class="mdi mdi-chevron-right text-muted" style="font-size: 1.25rem;"></i>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { listCategories, getCategoryDetail } from '../api/categories'
import { useContentLanguage } from '../composables/useContentLanguage'
import { breadcrumbCategoriesLabel } from '../utils/localizedLabels'
import { toPascalCaseWords } from '../utils/pascalCaseWords'

interface CategoryCard {
  id: string
  name: string
  iconUri: string
  itemCount: number
}

const router = useRouter()
const contentLanguage = useContentLanguage()
const loading = ref(true)
const error = ref('')
const cards = ref<CategoryCard[]>([])
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

onMounted(async () => {
  try {
    const summary = await listCategories()
    if (summary.error) {
      error.value = summary.error
      loading.value = false
      return
    }

    const names = summary.categories || []
    const cardList: CategoryCard[] = []

    for (const name of names) {
      try {
        const detail = await getCategoryDetail(name)
        cardList.push({
          id: name,
          name: name,
          iconUri: `/content/${name}/icon.png`,
          itemCount: detail.items?.length ?? 0,
        })
      } catch {
        cardList.push({
          id: name,
          name: name,
          iconUri: '/content/default/icon.png',
          itemCount: 0,
        })
      }
    }

    cards.value = cardList
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
