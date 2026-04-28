<script setup lang="ts">
import type { StockRecord } from '../types'

const props = defineProps<{
  record: StockRecord
  isNew?: boolean
  rankChange?: string
}>()
</script>

<template>
  <div class="stock-card" :class="{ 'card-new': isNew }">
    <div class="card-header">
      <span class="rank" :class="record.rank <= 3 ? `rank-${record.rank}` : ''">{{ record.rank }}</span>
      <span v-if="isNew" class="badge badge-new">NEW</span>
      <span v-if="rankChange" class="badge" :class="rankChange.includes('▲') ? 'badge-up' : 'badge-down'" v-html="rankChange" />
      <div class="info">
        <div class="name">{{ record.stock_name }}</div>
        <div class="code">{{ record.stock_code }}</div>
      </div>
      <div class="heat">{{ record.heat_value }}w</div>
    </div>
    <div class="change" :class="record.price_change_pct! >= 0 ? 'change-up' : 'change-down'">
      {{ record.price_change_pct! >= 0 ? '+' : '' }}{{ record.price_change_pct }}%
    </div>
    <div class="meta">
      <span>{{ record.turnover_amount || 0 }}亿</span>
    </div>
    <div class="holders">
      <span>今: {{ record.holders_today }}人</span>
      <span>昨: {{ record.holders_yesterday }}人</span>
      <span :class="(record.holders_today ?? 0) - (record.holders_yesterday ?? 0) >= 0 ? 'diff-up' : 'diff-down'">
        {{ (record.holders_today ?? 0) - (record.holders_yesterday ?? 0) >= 0 ? '+' : '' }}{{ (record.holders_today ?? 0) - (record.holders_yesterday ?? 0) }}
      </span>
    </div>
    <div class="tags">
      <span v-for="tag in record.sector_tags" :key="tag" class="tag">{{ tag }}</span>
    </div>
  </div>
</template>
