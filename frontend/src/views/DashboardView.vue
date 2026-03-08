<template>
  <div class="dashboard">
    <h1>Dashboard</h1>
    <p>This is a protected page. Only visible when logged in.</p>
    <div v-if="status">
      <h2>Service Status:</h2>
      <pre>{{ status }}</pre>
    </div>
    <button @click="fetchStatus">Refresh Status</button>

    <div v-if="filesData">
      <h2>Files in {{ filesData.directory }}:</h2>
      <ul v-if="filesData.files && filesData.files.length">
        <li v-for="file in filesData.files" :key="file">{{ file }}</li>
      </ul>
      <p v-else-if="filesData.error" style="color: red;">{{ filesData.error }}</p>
      <p v-else>No files found.</p>
    </div>
    <button @click="fetchFiles">Refresh File List</button>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getStatus, listFiles } from '../api/auth'

interface FilesData {
  directory?: string
  files?: string[]
  error?: string
  [key: string]: unknown
}

const status = ref<unknown>(null)
const filesData = ref<FilesData | null>(null)

const fetchStatus = async (): Promise<void> => {
  try {
    status.value = await getStatus()
  } catch (err) {
    console.error('Failed to fetch status', err)
  }
}

const fetchFiles = async (): Promise<void> => {
  try {
    filesData.value = await listFiles()
  } catch (err) {
    console.error('Failed to fetch files', err)
  }
}

onMounted(() => {
  fetchStatus()
  fetchFiles()
})
</script>
