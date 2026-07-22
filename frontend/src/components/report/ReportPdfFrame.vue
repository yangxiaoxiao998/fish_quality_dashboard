<script setup lang="ts">
defineProps<{
  src: string
}>()
</script>

<template>
  <iframe
    class="report-pdf-frame"
    :src="src"
  />
</template>

<style scoped>
.report-pdf-frame {
  width: 100%;
  height: 100%;
  min-height: 640px;
  border: none;
  background: #fff;
}
</style>
