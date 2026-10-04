<template>
  <div class="configuration-overview">
    <ViewBreadcrumb :items="[{ label: 'Configuration' }]" />
    <div class="page-header">
      <h3 class="page-title">Configuration</h3>
      <p class="text-muted">Select a service to manage its provider configurations.</p>
    </div>
    <div v-if="configurationServicesLoading" class="text-center py-5" role="status">
      <div class="spinner-border text-primary"></div>
      <p class="mt-2 text-muted">Loading services…</p>
    </div>
    <div v-else-if="configurationServicesError" class="text-center py-5" role="alert">
      <p class="text-danger">{{ configurationServicesError }}</p>
      <button type="button" class="btn btn-outline-primary btn-sm" @click="loadServices">Try again</button>
    </div>
    <p v-else-if="!configurationServices.length" class="text-muted text-center py-5">No configurable services are available.</p>
    <div v-else class="row">
      <div v-for="service in configurationServices" :key="service" class="col-md-6 col-xl-4 mb-4">
        <CollectionCard :title="serviceDisplayName(service)" :description="configurationCount(service)" :to="{ name: 'service-configuration', params: { serviceName: service } }">
          <template #icon><ServiceIcon :service="service" /></template>
        </CollectionCard>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import CollectionCard from '../components/CollectionCard.vue'
import ServiceIcon from '../components/ServiceIcon.vue'
import ViewBreadcrumb from '../components/ViewBreadcrumb.vue'
import { configurationServices, configurationServicesLoading, configurationServicesError, serviceConfigurationFiles, serviceConfigurationErrors, ensureConfigurationTree } from '../api/configurationCache'
import { serviceDisplayName } from '../utils/configurationNavigation'

function configurationCount(service: string): string {
  if (serviceConfigurationErrors.value[service]) return 'Configuration count unavailable'
  const files = serviceConfigurationFiles.value[service]
  if (!files) return 'Loading configurations…'
  return `${files.length} configuration${files.length === 1 ? '' : 's'} · ${files.filter(file => file.enabled).length} active`
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
