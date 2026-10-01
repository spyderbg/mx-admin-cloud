<template>
  <div class="cfg-fields" :class="{ 'cfg-fields-nested': depth > 0 }">
    <template v-for="[key, value] in entries" :key="key">
      <div v-if="isObject(value)" class="cfg-object-wrap">
        <details class="cfg-object" :open="depth < 1">
          <summary>
            <span>{{ label(key) }}</span>
            <small>{{ Object.keys(value).length }} {{ Object.keys(value).length === 1 ? 'field' : 'fields' }}</small>
          </summary>
          <DynamicConfigFields
            :model-value="value"
            :depth="depth + 1"
            :path="`${path}.${key}`"
            @update:model-value="updateObjectField(key, $event)"
            @validity="forwardValidity"
          />
        </details>
        <button type="button" class="cfg-remove-field cfg-remove-object" :aria-label="`Remove ${label(key)}`" @click="removeField(key)"><i class="mdi mdi-delete-outline" aria-hidden="true"></i></button>
      </div>
      <div v-else class="cfg-field" :class="{ 'cfg-field-wide': isWide(key, value) }">
        <div class="cfg-field-heading"><label :for="fieldId(key)">{{ label(key) }}</label><button type="button" class="cfg-remove-field" :aria-label="`Remove ${label(key)}`" @click="removeField(key)"><i class="mdi mdi-delete-outline" aria-hidden="true"></i></button></div>
        <div v-if="typeof value === 'boolean'" class="cfg-toggle-wrap">
          <input
            :id="fieldId(key)"
            type="checkbox"
            class="cfg-toggle"
            :checked="value"
            @change="updateField(key, ($event.target as HTMLInputElement).checked)"
          />
          <span>{{ value ? 'Enabled' : 'Disabled' }}</span>
        </div>
        <input
          v-else-if="typeof value === 'number'"
          :id="fieldId(key)"
          class="form-control"
          type="number"
          step="any"
          :value="value"
          @input="updateNumber(key, $event)"
        />
        <textarea
          v-else-if="typeof value === 'string' && isWide(key, value)"
          :id="fieldId(key)"
          class="form-control cfg-textarea"
          :value="value"
          rows="3"
          @input="updateField(key, ($event.target as HTMLTextAreaElement).value)"
        ></textarea>
        <input
          v-else-if="typeof value === 'string'"
          :id="fieldId(key)"
          class="form-control"
          type="text"
          :value="value"
          @input="updateField(key, ($event.target as HTMLInputElement).value)"
        />
        <textarea
          v-else
          :id="fieldId(key)"
          class="form-control cfg-textarea cfg-json-value"
          :class="{ 'is-invalid': errors[key] }"
          :value="JSON.stringify(value, null, 2)"
          rows="3"
          spellcheck="false"
          @input="updateJson(key, value, $event)"
        ></textarea>
        <small v-if="Array.isArray(value) || value === null" class="cfg-field-hint">JSON value</small>
        <small v-if="errors[key]" class="text-danger">{{ errors[key] }}</small>
      </div>
    </template>
    <div class="cfg-add-field">
      <button v-if="!adding" type="button" class="cfg-add-toggle" @click="adding = true"><i class="mdi mdi-plus" aria-hidden="true"></i> Add field</button>
      <div v-else class="cfg-add-controls">
        <input v-model="newKey" class="form-control" type="text" placeholder="Field name" aria-label="New field name" @keydown.enter.prevent="addField" />
        <select v-model="newType" class="form-control" aria-label="New field type">
          <option value="string">Text</option><option value="number">Number</option><option value="boolean">Boolean</option><option value="object">Object</option><option value="array">Array</option><option value="null">Null</option>
        </select>
        <button type="button" class="cfg-add-confirm" @click="addField">Add</button>
        <button type="button" class="cfg-add-cancel" aria-label="Cancel adding field" @click="cancelAdd">×</button>
        <small v-if="addError" class="text-danger">{{ addError }}</small>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: Record<string, unknown>
  depth?: number
  path?: string
}>(), { depth: 0, path: 'config' })

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
  validity: [path: string, valid: boolean]
}>()

const errors = reactive<Record<string, string>>({})
const adding = ref(false)
const newKey = ref('')
const newType = ref<'string' | 'number' | 'boolean' | 'object' | 'array' | 'null'>('string')
const addError = ref('')
const entries = computed(() => Object.entries(props.modelValue))
const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
const label = (key: string): string => key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())
const fieldId = (key: string): string => `cfg-${props.path}-${key}`.replace(/[^a-zA-Z0-9_-]/g, '-')
const isWide = (key: string, value: unknown): boolean =>
  Array.isArray(value) || value === null || (typeof value === 'string' && (value.length > 90 || /prompt|template|prefix|suffix|description/i.test(key)))

function updateField(key: string, value: unknown): void {
  delete errors[key]
  emit('validity', `${props.path}.${key}`, true)
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

function updateObjectField(key: string, value: Record<string, unknown>): void {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

function removeField(key: string): void {
  const next = { ...props.modelValue }
  delete next[key]
  delete errors[key]
  emit('validity', `${props.path}.${key}`, true)
  emit('update:modelValue', next)
}

function cancelAdd(): void {
  adding.value = false
  newKey.value = ''
  addError.value = ''
}

function addField(): void {
  const key = newKey.value.trim()
  if (!key) { addError.value = 'Enter a field name.'; return }
  if (Object.prototype.hasOwnProperty.call(props.modelValue, key)) { addError.value = 'This field already exists.'; return }
  const defaults: Record<typeof newType.value, unknown> = { string: '', number: 0, boolean: false, object: {}, array: [], null: null }
  emit('update:modelValue', { ...props.modelValue, [key]: defaults[newType.value] })
  cancelAdd()
}

function updateNumber(key: string, event: Event): void {
  const raw = (event.target as HTMLInputElement).value
  const value = Number(raw)
  if (raw.trim() === '' || !Number.isFinite(value)) {
    errors[key] = 'Enter a valid number.'
    emit('validity', `${props.path}.${key}`, false)
    return
  }
  updateField(key, value)
}

function updateJson(key: string, original: unknown, event: Event): void {
  const raw = (event.target as HTMLTextAreaElement).value
  try {
    const value: unknown = JSON.parse(raw)
    if (Array.isArray(original) && !Array.isArray(value)) throw new Error('Expected a JSON array.')
    updateField(key, value)
  } catch (error) {
    errors[key] = error instanceof Error ? error.message : 'Invalid JSON.'
    emit('validity', `${props.path}.${key}`, false)
  }
}

function forwardValidity(path: string, valid: boolean): void {
  emit('validity', path, valid)
}
</script>

<style scoped>
.cfg-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem 1.25rem; }
.cfg-fields-nested { padding-top: 1rem; }
.cfg-object-wrap, .cfg-field-wide, .cfg-add-field { grid-column: 1 / -1; }
.cfg-object-wrap { position: relative; min-width: 0; }
.cfg-object { border: 1px solid #e2e8f0; border-radius: 10px; padding: .8rem 1rem; background: #fff; }
.cfg-object summary { cursor: pointer; display: flex; align-items: center; justify-content: space-between; gap: 1rem; color: #172550; font-weight: 700; text-transform: capitalize; }
.cfg-object summary small { color: #7885a2; font-size: .76rem; font-weight: 500; margin-right: 1.6rem; }
.cfg-field-heading { display: flex; align-items: center; justify-content: space-between; gap: .4rem; min-height: 1.45rem; margin-bottom: .35rem; }
.cfg-field label { display: block; margin: 0; color: #263456; font-size: .82rem; font-weight: 600; overflow-wrap: anywhere; }
.cfg-remove-field { border: 0; background: none; color: #9aa6bd; cursor: pointer; padding: 0 .2rem; font-size: 1rem; line-height: 1; }
.cfg-remove-field:hover, .cfg-remove-field:focus-visible { color: #dc3545; }
.cfg-remove-object { position: absolute; right: .85rem; top: .8rem; }
.cfg-field .form-control { width: 100%; min-height: 38px; border-color: #d7dfed; border-radius: 7px; color: #172550; }
.cfg-textarea { resize: vertical; }
.cfg-json-value { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .8rem; }
.cfg-field-hint { display: block; margin-top: .25rem; color: #73809b; }
.cfg-toggle-wrap { min-height: 38px; display: flex; align-items: center; gap: .55rem; color: #667591; font-size: .83rem; }
.cfg-toggle { width: 18px; height: 18px; accent-color: #1c45ef; }
.cfg-add-field { margin-top: .2rem; }
.cfg-add-toggle, .cfg-add-confirm { border: 1px solid #96b5fb; border-radius: 6px; background: #f9fbff; color: #1951d8; padding: .38rem .7rem; font-size: .78rem; font-weight: 700; cursor: pointer; }
.cfg-add-toggle:hover, .cfg-add-confirm:hover { background: #eaf1ff; }
.cfg-add-controls { display: flex; flex-wrap: wrap; align-items: center; gap: .45rem; }
.cfg-add-controls input { flex: 1 1 9rem; min-width: 0; }
.cfg-add-controls select { flex: 0 1 8rem; min-width: 0; }
.cfg-add-controls .form-control { min-height: 34px; font-size: .8rem; }
.cfg-add-cancel { border: 0; background: none; color: #7d89a0; font-size: 1.3rem; line-height: 1; cursor: pointer; }
.cfg-add-controls small { width: 100%; }
@media (max-width: 680px) { .cfg-fields { grid-template-columns: minmax(0, 1fr); gap: .85rem; } }
</style>
