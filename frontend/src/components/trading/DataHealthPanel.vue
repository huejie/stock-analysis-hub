<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useTradingApi } from '../../composables/useTradingApi'
import type { DataHealth } from '../../types/trading'

const api = useTradingApi()
const health = ref<DataHealth | null>(null)
const loading = ref(false)
const error = ref('')

async function loadHealth() {
  loading.value = true
  error.value = ''
  try {
    health.value = await api.getDataHealth()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

const statusColor = (s: string): string => ({
  OK: '#10b981', PARTIAL: '#f59e0b', BLOCKED: '#ef4444',
}[s] ?? '#888')

onMounted(loadHealth)
</script>

<template>
  <div class="panel">
    <div class="header">
      <h3>数据健康</h3>
      <button class="btn-refresh" :disabled="loading" @click="loadHealth">
        {{ loading ? '刷新中...' : '刷新' }}
      </button>
    </div>

    <p v-if="error" class="msg error">{{ error }}</p>

    <div v-if="health" class="health-content">
      <div class="status-banner" :style="{ borderColor: statusColor(health.overall_status) }">
        <span class="status-label" :style="{ color: statusColor(health.overall_status) }">
          {{ health.overall_status }}
        </span>
        <span class="trade-date">交易日: {{ health.trade_date }}</span>
      </div>

      <div class="metrics">
        <div class="metric">
          <span class="metric-label">基准指数</span>
          <div v-for="code in health.benchmark_codes" :key="code" class="benchmark-row">
            <span>{{ code }}</span>
            <span :class="health.benchmark_updated[code] ? 'ok' : 'bad'">
              {{ health.benchmark_updated[code] ? '已更新' : '未更新' }}
            </span>
          </div>
        </div>

        <div class="metric">
          <span class="metric-label">股票池覆盖</span>
          <span>{{ health.pool_available }} / {{ health.pool_total }}
            (缺失 {{ (health.pool_missing_ratio * 100).toFixed(1) }}%)</span>
        </div>
      </div>

      <div v-if="health.pool_missing.length" class="missing">
        <h4>缺失股票 ({{ health.pool_missing.length }})</h4>
        <div class="missing-codes">
          <span v-for="code in health.pool_missing" :key="code" class="code-chip">{{ code }}</span>
        </div>
      </div>

      <div v-if="health.issues.length" class="issues">
        <h4>问题列表</h4>
        <div
          v-for="(issue, i) in health.issues"
          :key="i"
          class="issue"
          :class="issue.severity.toLowerCase()"
        >
          <span class="issue-severity">{{ issue.severity }}</span>
          <span class="issue-code">{{ issue.issue_code }}</span>
          <span class="issue-msg">{{ issue.message }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel { padding: 1rem; }
.header { display: flex; justify-content: space-between; align-items: center; }
.btn-refresh { padding: 0.25rem 0.75rem; background: #333; color: #ccc; border: 1px solid #444; border-radius: 4px; cursor: pointer; }
.btn-refresh:disabled { opacity: 0.5; cursor: not-allowed; }
.msg.error { background: #451a1a; color: #fca5a5; padding: 0.5rem; border-radius: 4px; }
.status-banner { display: flex; align-items: center; gap: 1rem; padding: 0.75rem; border-left: 4px solid; background: #1a1a1a; border-radius: 4px; margin: 0.75rem 0; }
.status-label { font-weight: bold; font-size: 1.1rem; }
.trade-date { color: #888; }
.metrics { display: flex; gap: 2rem; margin: 1rem 0; flex-wrap: wrap; }
.metric { display: flex; flex-direction: column; gap: 0.25rem; }
.metric-label { color: #888; font-size: 0.85rem; }
.benchmark-row { display: flex; justify-content: space-between; gap: 1rem; min-width: 140px; }
.ok { color: #10b981; }
.bad { color: #ef4444; }
.missing { margin: 1rem 0; }
.missing-codes { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.code-chip { background: #2a1a1a; color: #fca5a5; padding: 0.15rem 0.5rem; border-radius: 3px; font-size: 0.85rem; font-family: monospace; }
.issues { margin-top: 1rem; }
.issue { display: grid; grid-template-columns: 80px 160px 1fr; gap: 0.5rem; padding: 0.4rem; border-bottom: 1px solid #2a2a2a; font-size: 0.9rem; align-items: start; }
.issue.warning { background: #422006; }
.issue.blocking { background: #451a1a; }
.issue-severity { font-weight: bold; }
.issue.blocking .issue-severity { color: #fca5a5; }
.issue.warning .issue-severity { color: #fcd34d; }
.issue-code { color: #888; font-family: monospace; font-size: 0.85rem; }
.issue-msg { word-break: break-all; }

@media (max-width: 600px) {
  .issue { grid-template-columns: 1fr; gap: 0.15rem; }
  .metrics { flex-direction: column; gap: 0.75rem; }
}
</style>
