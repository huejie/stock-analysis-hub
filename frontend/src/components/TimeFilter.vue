<script setup lang="ts">
import type { PresetDays } from '../types'

const props = defineProps<{
  presets: { label: string; days: number | 'custom' }[]
  modelValue: PresetDays
}>()

const emit = defineEmits<{
  'update:modelValue': [value: PresetDays]
  'query': []
}>()

function select(days: number | string) {
  emit('update:modelValue', (days === 'custom' ? 'custom' : days) as PresetDays)
  if (days !== 'custom') emit('query')
}
</script>

<template>
  <div class="pnl-filter">
    <div class="pnl-presets">
      <button
        v-for="p in presets"
        :key="p.days"
        class="btn btn-outline btn-sm"
        :class="{ active: modelValue === p.days }"
        @click="select(p.days)"
      >
        {{ p.label }}
      </button>
    </div>
    <div v-if="modelValue === 'custom'" class="pnl-custom-range">
      <slot />
      <button class="btn btn-primary btn-sm" @click="$emit('query')">查询</button>
    </div>
  </div>
</template>
