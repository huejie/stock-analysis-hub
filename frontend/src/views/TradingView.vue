<script setup lang="ts">
import { ref } from 'vue'
import StockPoolPanel from '../components/trading/StockPoolPanel.vue'
import DataHealthPanel from '../components/trading/DataHealthPanel.vue'
import TradingDashboard from '../components/trading/TradingDashboard.vue'
import DailyPlanPanel from '../components/trading/DailyPlanPanel.vue'
import AccountPanel from '../components/trading/AccountPanel.vue'
import PortfolioPanel from '../components/trading/PortfolioPanel.vue'
import StrategySettingsPanel from '../components/trading/StrategySettingsPanel.vue'

// 二级子页(spec §12.1):总览/次日计划/股票池/持仓与成交/回测与复盘[Phase5]/数据健康/策略设置
type SubTab = 'overview' | 'plan' | 'pool' | 'positions' | 'backtest' | 'health' | 'strategy'
const subTab = ref<SubTab>('overview')

// 选中的账户 id,跨面板共享(AccountPanel 选择 → PortfolioPanel/PlanPanel/Dashboard 使用)
const selectedAccountId = ref<number | null>(null)

function handleAccountSelect(id: number) {
  selectedAccountId.value = id
}

const tabs: { key: SubTab; label: string; disabled?: boolean }[] = [
  { key: 'overview', label: '总览' },
  { key: 'plan', label: '次日计划' },
  { key: 'pool', label: '股票池' },
  { key: 'positions', label: '持仓与成交' },
  { key: 'backtest', label: '回测与复盘', disabled: true },
  { key: 'health', label: '数据健康' },
  { key: 'strategy', label: '策略设置' },
]
</script>

<template>
  <div class="trading-view">
    <div class="sub-tabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        :class="{ active: subTab === t.key, disabled: t.disabled }"
        :disabled="t.disabled"
        @click="!t.disabled && (subTab = t.key)"
        :title="t.disabled ? 'Phase 5 实现' : ''"
      >
        {{ t.label }}<span v-if="t.key === 'backtest'" class="phase-tag">P5</span>
      </button>
    </div>

    <div class="sub-content">
      <TradingDashboard v-if="subTab === 'overview'" :account-id="selectedAccountId" />

      <!-- 次日计划:需要账户选择 -->
      <div v-else-if="subTab === 'plan'" class="with-account">
        <div class="account-bar">
          <AccountPanel @select="handleAccountSelect" />
        </div>
        <DailyPlanPanel :account-id="selectedAccountId" />
      </div>

      <!-- 股票池:无账户依赖 -->
      <StockPoolPanel v-else-if="subTab === 'pool'" />

      <!-- 持仓与成交:账户选择 + 持仓面板 -->
      <div v-else-if="subTab === 'positions'" class="with-account">
        <AccountPanel @select="handleAccountSelect" />
        <PortfolioPanel :account-id="selectedAccountId" />
      </div>

      <!-- 回测与复盘:Phase 5 -->
      <div v-else-if="subTab === 'backtest'" class="placeholder">
        <p>回测与复盘将在 Phase 5 实现。</p>
        <p class="phase-hint">包括:回测任务、费用模型、统计指标、策略激活门禁、计划与实际成交对比、规则执行率与 R 倍数。</p>
      </div>

      <!-- 数据健康 -->
      <DataHealthPanel v-else-if="subTab === 'health'" />

      <!-- 策略设置 -->
      <StrategySettingsPanel v-else-if="subTab === 'strategy'" />
    </div>
  </div>
</template>

<style scoped>
.trading-view { color: #eee; }
.sub-tabs { display: flex; gap: 0.5rem; border-bottom: 1px solid #333; margin-bottom: 1rem; flex-wrap: wrap; }
.sub-tabs button { padding: 0.5rem 1rem; background: transparent; color: #888; border: none; border-bottom: 2px solid transparent; cursor: pointer; font-size: 0.95rem; }
.sub-tabs button:hover:not(.disabled) { color: #ccc; }
.sub-tabs button.active { color: #3b82f6; border-bottom-color: #3b82f6; }
.sub-tabs button.disabled { color: #444; cursor: not-allowed; }
.phase-tag { font-size: 0.65rem; background: #333; color: #666; padding: 0 0.2rem; border-radius: 2px; margin-left: 0.2rem; }
.sub-content { min-height: 300px; }
.with-account { display: flex; flex-direction: column; gap: 1rem; }
.account-bar { border-bottom: 1px solid #2a2a2a; padding-bottom: 0.75rem; }
.placeholder { padding: 2rem; color: #666; text-align: center; }
.phase-hint { font-size: 0.85rem; margin-top: 0.5rem; color: #555; }
</style>
