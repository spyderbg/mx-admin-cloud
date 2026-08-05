<template>
  <div class="mock-data-badge" title="The application is using local mock data">
    <span class="mock-data-badge__icon">!</span>
    <span>MOCK DATA</span>
  </div>
</template>

<script setup lang="ts">

</script>

<style scoped>
.mock-data-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;

  height: 34px;
  padding: 0 12px;

  color: #e86f00;
  background: #fff8ed;
  border: 1px solid #f5a623;
  border-radius: 6px;

  font-size: 13px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
}

.mock-data-badge__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;

  width: 17px;
  height: 17px;

  color: white;
  background: #f59e0b;
  border-radius: 50%;

  font-size: 12px;
  font-weight: 700;
}
</style>