<template>
  <Teleport to="body">
    <div class="cd-modal-overlay" @click.self="cancel">
      <div class="cd-modal-box cfg-modal-box">
        <div class="cd-modal-header">
          <h5 class="cd-modal-title">{{ serviceLabel(service) }} Configuration</h5>
          <button type="button" class="cd-modal-close" @click="cancel">&times;</button>
        </div>
        <div class="cd-modal-body">
          <div v-if="loadError" class="text-danger cfg-status">{{ loadError }}</div>
          <div v-else-if="loading" class="text-muted cfg-status">Loading…</div>
          <div v-else-if="rows.length === 0" class="text-muted cfg-status">
            No configuration files found for this service.
          </div>

          <div v-if="rows.length" class="table-responsive">
            <table class="table cfg-table">
              <thead>
                <tr>
                  <th>Configuration</th>
                  <th>Provider</th>
                  <th class="text-center">Enabled</th>
                  <th>Priority</th>
                  <th>Version</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in rows" :key="row.summary.config_name">
                  <td class="cfg-name-cell">
                    {{ row.summary.config_name }}
                    <small v-if="!row.summary.valid_json" class="text-danger d-block">invalid JSON</small>
                  </td>
                  <td>{{ row.summary.provider ?? '—' }}</td>
                  <td class="text-center">
                    <input
                      v-model="row.enabled"
                      type="checkbox"
                      :disabled="!row.summary.valid_json || saving"
                    />
                  </td>
                  <td>
                    <input
                      v-model.number="row.priority"
                      type="number"
                      min="1"
                      class="form-control form-control-sm cfg-priority-input"
                      :disabled="!row.summary.valid_json || saving"
                    />
                  </td>
                  <td>
                    <input
                      v-model="row.version"
                      type="text"
                      class="form-control form-control-sm cfg-version-input"
                      placeholder="—"
                      :disabled="!row.summary.valid_json || saving"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <div class="cd-modal-footer">
          <button type="button" class="btn btn-secondary" :disabled="saving" @click="cancel">Cancel</button>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="!hasChanges || saving"
            @click="confirmChanges"
          >
            {{ saving ? 'Saving…' : 'Confirm' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Error toast (failures keep the modal open) -->
  <Teleport to="body">
    <div v-if="toast" class="cd-toast cd-toast-error">
      <i class="mdi mdi-alert-circle"></i>
      <span>{{ toast }}</span>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { AxiosError } from 'axios'
import { listServiceConfigs, patchServiceConfig } from '../api/configurationClient'
import type { ConfigurationFileSummaryResponse } from '../types/configurationSchemas'

const props = defineProps<{
  service: string
}>()

const emit = defineEmits<{
  close: []
  saved: [message: string]
}>()

interface ConfigRow {
  summary: ConfigurationFileSummaryResponse
  enabled: boolean
  priority: number | null
  version: string
}

const rows = ref<ConfigRow[]>([])
const loading = ref(false)
const loadError = ref<string | null>(null)
const saving = ref(false)

const toast = ref<string | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showErrorToast(message: string): void {
  console.error(message)
  if (toastTimer) clearTimeout(toastTimer)
  toast.value = message
  toastTimer = setTimeout(() => { toast.value = null }, 4000)
}

function extractErrorMessage(err: unknown): string {
  const axiosErr = err as AxiosError<{ detail?: string }>
  return axiosErr.response?.data?.detail || 'The operation failed. Please try again.'
}

function serviceLabel(service: string): string {
  return service
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function toRow(summary: ConfigurationFileSummaryResponse): ConfigRow {
  return {
    summary,
    enabled: summary.enabled ?? false,
    priority: summary.priority ?? null,
    version: summary.version ?? '',
  }
}

function changedFields(row: ConfigRow): Record<string, boolean | number | string> {
  const fields: Record<string, boolean | number | string> = {}
  if (row.enabled !== (row.summary.enabled ?? false)) fields.enabled = row.enabled
  if (
    typeof row.priority === 'number' &&
    Number.isFinite(row.priority) &&
    row.priority !== (row.summary.priority ?? null)
  ) {
    fields.priority = row.priority
  }
  if (row.version !== (row.summary.version ?? '')) fields.version = row.version
  return fields
}

function isDirty(row: ConfigRow): boolean {
  return Object.keys(changedFields(row)).length > 0
}

const hasChanges = computed(() => rows.value.some(isDirty))

async function loadRows(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const summaries = await listServiceConfigs(props.service)
    rows.value = summaries.map(toRow)
  } catch (err) {
    rows.value = []
    loadError.value = extractErrorMessage(err)
  } finally {
    loading.value = false
  }
}

function cancel(): void {
  if (saving.value) return
  emit('close')
}

async function confirmChanges(): Promise<void> {
  const dirty = rows.value.filter(isDirty)
  if (!dirty.length || saving.value) return
  saving.value = true
  const failures: string[] = []
  for (const row of dirty) {
    try {
      await patchServiceConfig(props.service, row.summary.config_name, {
        patch: { service_provider: changedFields(row) },
      })
    } catch (err) {
      failures.push(`${row.summary.config_name}: ${extractErrorMessage(err)}`)
    }
  }
  saving.value = false
  if (failures.length) {
    const extra = failures.length > 1 ? ` (+${failures.length - 1} more)` : ''
    showErrorToast(failures[0] + extra)
    await loadRows()
    return
  }
  emit('saved', `${serviceLabel(props.service)} configuration updated.`)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') cancel()
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  loadRows()
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  if (toastTimer) clearTimeout(toastTimer)
})
</script>

<style scoped>
/* Modal styles — prefixed to avoid Star Admin .close conflicts */
.cd-modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1050;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}
.cd-modal-box {
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 5px 30px rgba(0, 0, 0, 0.3);
  width: 100%;
  max-width: 480px;
  margin: 1rem;
  overflow: hidden;
}
.cfg-modal-box {
  max-width: 720px;
}
.cd-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid #dee2e6;
}
.cd-modal-title {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 600;
}
.cd-modal-close {
  background: none;
  border: none;
  font-size: 1.4rem;
  cursor: pointer;
  padding: 0;
  line-height: 1;
  color: #6b7280;
  flex-shrink: 0;
}
.cd-modal-close:hover {
  color: #111;
}
.cd-modal-body {
  padding: 1.25rem;
  max-height: 70vh;
  overflow-y: auto;
}
.cd-modal-footer {
  padding: 0.75rem 1.25rem;
  border-top: 1px solid #dee2e6;
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

/* Config table */
.cfg-status {
  padding: 0.5rem 0;
}
.cfg-table td {
  vertical-align: middle;
}
.cfg-name-cell {
  font-weight: 600;
}
.cfg-priority-input {
  width: 5.5rem;
}
.cfg-version-input {
  width: 6.5rem;
}

/* Result toast */
.cd-toast {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  z-index: 99999;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  max-width: 360px;
  padding: 0.75rem 1rem;
  border-radius: 6px;
  color: #fff;
  font-size: 0.9rem;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
}
.cd-toast i {
  font-size: 1.2rem;
  flex-shrink: 0;
}
.cd-toast-error {
  background: #dc2626;
}
</style>
