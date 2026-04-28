<script setup lang="ts">
import { ref } from 'vue'
import { useChart } from '../composables/useChart'

defineProps<{ title: string; height?: string }>()

const containerRef = ref<HTMLElement | null>(null)
const { setOption, dispose } = useChart(containerRef)

defineExpose({ setOption, dispose })
</script>

<template>
  <div class="chart-box">
    <h3>{{ title }}</h3>
    <div
      ref="containerRef"
      class="chart-container"
      :style="{ height: height || '340px' }"
    />
  </div>
</template>
