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
      <div class="info">
        <div class="name-row">
          <span class="name">{{ record.stock_name }}</span>
          <span class="code">{{ record.stock_code }}</span>
        </div>
        <div class="badges-row">
          <span v-if="isNew" class="badge badge-new">NEW</span>
          <span v-if="rankChange" class="badge" :class="rankChange.includes('▲') ? 'badge-up' : 'badge-down'" v-html="rankChange" />
        </div>
      </div>
    </div>
    <div class="card-body">
      <div class="change" :class="record.price_change_pct! >= 0 ? 'change-up' : 'change-down'">
        {{ record.price_change_pct! >= 0 ? '+' : '' }}{{ record.price_change_pct }}%
      </div>
      <div class="stats-row">
        <span class="stat"><span class="stat-label">热度</span>{{ record.heat_value }}w</span>
        <span class="stat"><span class="stat-label">成交</span>{{ record.turnover_amount || 0 }}亿</span>
      </div>
      <div class="holders">
        <span>今 {{ record.holders_today }}</span>
        <span>昨 {{ record.holders_yesterday }}</span>
        <span :class="(record.holders_today ?? 0) - (record.holders_yesterday ?? 0) >= 0 ? 'diff-up' : 'diff-down'">
          {{ (record.holders_today ?? 0) - (record.holders_yesterday ?? 0) >= 0 ? '+' : '' }}{{ (record.holders_today ?? 0) - (record.holders_yesterday ?? 0) }}
        </span>
      </div>
      <div class="tags">
        <span v-for="tag in record.sector_tags" :key="tag" class="tag">{{ tag }}</span>
      </div>
    </div>
  </div>
</template>
