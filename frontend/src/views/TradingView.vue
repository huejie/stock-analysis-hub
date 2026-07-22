<script setup lang="ts">
import { ref } from 'vue'
import StockPoolPanel from '../components/trading/StockPoolPanel.vue'
import DataHealthPanel from '../components/trading/DataHealthPanel.vue'

// Phase 1 子页面:股票池 / 数据健康。
// 总览 / 持仓 / 回测 / 策略设置 等子页面留待 Phase 2-5 实现。
type SubTab = 'pool' | 'health' | 'placeholder'
const subTab = ref<SubTab>('pool')

const tabs: { key: SubTab; label: string }[] = [
  { key: 'pool', label: '股票池' },
  { key: 'health', label: '数据健康' },
]
</script>

<template>
  <div class="trading-view">
    <div class="sub-tabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        :class="{ active: subTab === t.key }"
        @click="subTab = t.key"
      >
        {{ t.label }}
      </button>
    </div>

    <div class="sub-content">
      <StockPoolPanel v-if="subTab === 'pool'" />
      <DataHealthPanel v-else-if="subTab === 'health'" />
      <div v-else class="placeholder">
        <p>该子页面将在后续 Phase 实现。</p>
        <p class="phase-hint">Phase 1 已交付:股票池 + 数据健康。账户/持仓(Phase 2)、计划生成(Phase 3)、回测复盘(Phase 5)敬请期待。</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trading-view { color: #eee; }
.sub-tabs { display: flex; gap: 0.5rem; border-bottom: 1px solid #333; margin-bottom: 1rem; flex-wrap: wrap; }
.sub-tabs button { padding: 0.5rem 1rem; background: transparent; color: #888; border: none; border-bottom: 2px solid transparent; cursor: pointer; font-size: 0.95rem; }
.sub-tabs button:hover { color: #ccc; }
.sub-tabs button.active { color: #3b82f6; border-bottom-color: #3b82f6; }
.placeholder { padding: 2rem; color: #666; text-align: center; }
.phase-hint { font-size: 0.85rem; margin-top: 0.5rem; color: #555; }
</style>
