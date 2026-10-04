<template>
  <div class="configuration-overview">
    <ViewBreadcrumb :items="[{ label: configurationLabel(contentLanguage) }]" />
    <div class="page-header">
      <h3 class="page-title">{{ configurationLabel(contentLanguage) }}</h3>
      <p class="text-muted">{{ labels.description }}</p>
    </div>
    <div v-if="configurationServicesLoading" class="text-center py-5" role="status">
      <div class="spinner-border text-primary"></div>
      <p class="mt-2 text-muted">{{ loadingServicesLabel(contentLanguage) }}</p>
    </div>
    <div v-else-if="configurationServicesError" class="text-center py-5" role="alert">
      <p class="text-danger">{{ failedToLoadServicesLabel(contentLanguage) }}</p>
      <button type="button" class="btn btn-outline-primary btn-sm" @click="loadServices">{{ labels.tryAgain }}</button>
    </div>
    <p v-else-if="!configurationServices.length" class="text-muted text-center py-5">{{ labels.noServices }}</p>
    <div v-else class="row">
      <div v-for="service in configurationServices" :key="service" class="col-md-6 col-xl-4 mb-4">
        <CollectionCard :title="localizedServiceLabel(service, contentLanguage)" :description="configurationCount(service)" :to="{ name: 'service-configuration', params: { serviceName: service } }">
          <template #icon><ServiceIcon :service="service" /></template>
        </CollectionCard>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import CollectionCard from '../components/CollectionCard.vue'
import ServiceIcon from '../components/ServiceIcon.vue'
import ViewBreadcrumb from '../components/ViewBreadcrumb.vue'
import { configurationServices, configurationServicesLoading, configurationServicesError, serviceConfigurationFiles, serviceConfigurationErrors, ensureConfigurationTree } from '../api/configurationCache'
import { useContentLanguage } from '../composables/useContentLanguage'
import { configurationLabel, loadingServicesLabel, failedToLoadServicesLabel } from '../utils/localizedLabels'
import { configurationLabels, configurationCountLabel, localizedServiceLabel } from '../utils/localizedConfigurations'

const contentLanguage = useContentLanguage()
const labels = computed(() => configurationLabels(contentLanguage.value))

function configurationCount(service: string): string {
  if (serviceConfigurationErrors.value[service]) return labels.value.countUnavailable
  const files = serviceConfigurationFiles.value[service]
  if (!files) return labels.value.loadingConfigurations
  return configurationCountLabel(contentLanguage.value, files.length, files.filter(file => file.enabled).length)
}

async function loadServices(): Promise<void> {
  await ensureConfigurationTree().catch(() => undefined)
}

onMounted(loadServices)
</script>

<style scoped>
.configuration-overview .page-header { display: block; }
.configuration-overview .page-title { margin-bottom: .35rem; }
.configuration-overview .page-header p { margin: 0; font-size: .875rem; }
</style>
