<script setup lang="ts">
defineProps<{ icon?: string; text?: string }>()
</script>

<template>
  <div class="empty-state" style="display:flex">
    <div class="empty-icon">{{ icon || 'S' }}</div>
    <h2>{{ text || '暂无数据' }}</h2>
  </div>
</template>
