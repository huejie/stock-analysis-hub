<script setup lang="ts">
import { ref, watch } from 'vue'
import { useTradingApi } from '../../composables/useTradingApi'
import { useFormat } from '../../composables/useFormat'
import type { Position, Execution, EquitySnapshot, ExecutionCreateRequest } from '../../types/trading'

const props = defineProps<{ accountId: number | null }>()
const api = useTradingApi()
const { formatMoney, formatPercent, formatPrice, formatQty } = useFormat()

const positions = ref<Position[]>([])
const executions = ref<Execution[]>([])
const equity = ref<EquitySnapshot | null>(null)

const loadingPos = ref(false)
const loadingExec = ref(false)
const loadingEquity = ref(false)
const submitting = ref(false)
const error = ref('')
const success = ref('')

// 成交录入表单
function todayStr(): string {
  const d = new Date()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

function resetForm() {
  execForm.value = {
    account_id: props.accountId ?? 0,
    stock_code: '',
    side: 'BUY',
    trade_date: todayStr(),
    price: 0,
    quantity: 0,
    commission: 0,
    tax: 0,
    note: '',
    client_execution_id: '',
  }
}

const execForm = ref<ExecutionCreateRequest>({
  account_id: 0,
  stock_code: '',
  side: 'BUY',
  trade_date: todayStr(),
  price: 0,
  quantity: 0,
  commission: 0,
  tax: 0,
  note: '',
  client_execution_id: '',
})

async function loadPositions() {
  if (props.accountId == null) return
  loadingPos.value = true
  error.value = ''
  try {
    const res = await api.getPositions(props.accountId)
    positions.value = res.positions
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loadingPos.value = false
  }
}

async function loadExecutions() {
  if (props.accountId == null) return
  loadingExec.value = true
  try {
    const res = await api.listExecutions(props.accountId)
    executions.value = res.executions
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loadingExec.value = false
  }
}

async function loadAll() {
  await Promise.all([loadPositions(), loadExecutions()])
}

async function handleRecord() {
  if (props.accountId == null) {
    error.value = '请先选择账户'
    return
  }
  if (!execForm.value.stock_code.trim()) {
    error.value = '请输入股票代码'
    return
  }
  if (execForm.value.quantity <= 0 || execForm.value.quantity % 100 !== 0) {
    error.value = '数量必须是 100 的整数倍'
    return
  }
  if (execForm.value.price <= 0) {
    error.value = '请输入有效价格'
    return
  }

  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    const payload: ExecutionCreateRequest = {
      ...execForm.value,
      account_id: props.accountId,
      client_execution_id: crypto.randomUUID(),
    }
    const result = await api.recordExecution(payload)
    success.value = `成交已记录:${result.execution.side === 'BUY' ? '买入' : '卖出'} ${result.execution.stock_code} ${formatQty(result.execution.quantity)} 股 @ ${formatPrice(result.execution.price)}`
    resetForm()
    await loadPositions()
    await loadExecutions()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    submitting.value = false
  }
}

async function computeEquity() {
  if (props.accountId == null) return
  loadingEquity.value = true
  error.value = ''
  try {
    equity.value = await api.getEquitySnapshot(props.accountId, todayStr())
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loadingEquity.value = false
  }
}

// accountId 变化时重新加载
watch(
  () => props.accountId,
  (id) => {
    if (id != null) {
      resetForm()
      void loadAll()
      equity.value = null
    } else {
      positions.value = []
      executions.value = []
      equity.value = null
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="panel">
    <div v-if="accountId == null" class="empty-prompt">
      <p>请先选择账户</p>
      <p class="hint">在「账户管理」中选择或创建一个账户后,即可查看持仓与成交。</p>
    </div>

    <template v-else>
      <div class="header">
        <h3>持仓与成交</h3>
        <button class="btn-secondary" :disabled="loadingPos || loadingExec" @click="loadAll">
          {{ loadingPos || loadingExec ? '刷新中...' : '刷新' }}
        </button>
      </div>

      <p v-if="error" class="msg error">{{ error }}</p>
      <p v-if="success" class="msg success">{{ success }}</p>

      <!-- 持仓表 -->
      <section class="section">
        <h4>当前持仓</h4>
        <p v-if="loadingPos" class="text-muted">加载中...</p>
        <p v-else-if="!positions.length" class="text-muted">暂无持仓</p>
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>代码</th><th>名称</th><th>持仓量</th><th>可用量</th>
                <th>成本价</th><th>初始止损</th><th>跟踪止损</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in positions" :key="p.id">
                <td class="mono">{{ p.stock_code }}</td>
                <td>{{ p.stock_name || '-' }}</td>
                <td>{{ formatQty(p.quantity) }}</td>
                <td>{{ formatQty(p.available_quantity) }}</td>
                <td>{{ formatPrice(p.average_cost) }}</td>
                <td>{{ formatPrice(p.initial_stop) }}</td>
                <td>{{ formatPrice(p.trailing_stop) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- 成交录入 -->
      <section class="section">
        <h4>录入成交</h4>
        <div class="exec-form">
          <div class="form-field">
            <label>方向</label>
            <select v-model="execForm.side" :class="execForm.side === 'BUY' ? 'side-buy' : 'side-sell'">
              <option value="BUY">买入(买)</option>
              <option value="SELL">卖出(卖)</option>
            </select>
          </div>
          <div class="form-field">
            <label>股票代码</label>
            <input v-model="execForm.stock_code" type="text" placeholder="如 000001" class="mono" />
          </div>
          <div class="form-field">
            <label>成交日期</label>
            <input v-model="execForm.trade_date" type="date" />
          </div>
          <div class="form-field">
            <label>价格</label>
            <input v-model.number="execForm.price" type="number" min="0" step="0.001" />
          </div>
          <div class="form-field">
            <label>数量(100 整数倍)</label>
            <input v-model.number="execForm.quantity" type="number" min="0" step="100" />
          </div>
          <div class="form-field">
            <label>佣金</label>
            <input v-model.number="execForm.commission" type="number" min="0" step="0.01" />
          </div>
          <div class="form-field">
            <label>印花税</label>
            <input v-model.number="execForm.tax" type="number" min="0" step="0.01" />
          </div>
          <div class="form-field form-field-wide">
            <label>备注</label>
            <input v-model="execForm.note" type="text" placeholder="可选" />
          </div>
        </div>
        <div class="form-actions">
          <button
            class="btn-primary"
            :class="execForm.side === 'BUY' ? 'btn-buy' : 'btn-sell'"
            :disabled="submitting"
            @click="handleRecord"
          >
            {{ submitting ? '提交中...' : (execForm.side === 'BUY' ? '买入(买)' : '卖出(卖)') }}
          </button>
        </div>
        <p class="hint">client_execution_id 由前端自动生成(crypto.randomUUID),用于幂等防重。</p>
      </section>

      <!-- 最近成交 -->
      <section class="section">
        <h4>最近成交</h4>
        <p v-if="loadingExec" class="text-muted">加载中...</p>
        <p v-else-if="!executions.length" class="text-muted">暂无成交记录</p>
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>方向</th><th>代码</th><th>日期</th><th>价格</th>
                <th>数量</th><th>佣金</th><th>印花税</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="ex in executions" :key="ex.id">
                <td>
                  <span class="side-tag" :class="ex.side === 'BUY' ? 'tag-buy' : 'tag-sell'">
                    {{ ex.side === 'BUY' ? '买' : '卖' }}
                  </span>
                </td>
                <td class="mono">{{ ex.stock_code }}</td>
                <td>{{ ex.trade_date }}</td>
                <td>{{ formatPrice(ex.price) }}</td>
                <td>{{ formatQty(ex.quantity) }}</td>
                <td>{{ formatMoney(ex.commission) }}</td>
                <td>{{ formatMoney(ex.tax) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- 净值快照 -->
      <section class="section">
        <div class="section-header">
          <h4>净值快照</h4>
          <button class="btn-secondary" :disabled="loadingEquity" @click="computeEquity">
            {{ loadingEquity ? '计算中...' : (equity ? '刷新快照' : '计算快照') }}
          </button>
        </div>
        <div v-if="equity" class="metrics-grid">
          <div class="metric">
            <span class="metric-label">交易日期</span>
            <span class="metric-value">{{ equity.trade_date }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">现金</span>
            <span class="metric-value">{{ formatMoney(equity.cash) }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">市值</span>
            <span class="metric-value">{{ formatMoney(equity.market_value) }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">总权益</span>
            <span class="metric-value total">{{ formatMoney(equity.total_equity) }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">总敞口</span>
            <span class="metric-value">{{ formatPercent(equity.exposure) }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">峰值权益</span>
            <span class="metric-value">{{ formatMoney(equity.peak_equity) }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">回撤</span>
            <span class="metric-value" :class="equity.drawdown > 0 ? 'drawdown-warn' : ''">
              {{ formatPercent(equity.drawdown) }}
            </span>
          </div>
        </div>
        <p v-else class="text-muted">点击「计算快照」生成当日净值。</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.panel { padding: 1rem; }
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }

.empty-prompt { padding: 2rem; text-align: center; color: #888; border: 1px dashed #333; border-radius: 6px; }
.empty-prompt .hint { font-size: 0.85rem; color: #555; margin-top: 0.5rem; }

.msg { padding: 0.5rem; border-radius: 4px; margin-bottom: 0.75rem; }
.msg.error { background: #451a1a; color: #fca5a5; }
.msg.success { background: #14532d; color: #86efac; }

.section { margin-bottom: 1.5rem; }
.section h4 { margin: 0 0 0.5rem; color: #ccc; }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; }
.section-header h4 { margin: 0; }

.text-muted { color: #888; }
.hint { color: #666; font-size: 0.8rem; margin-top: 0.25rem; }
.mono { font-family: monospace; }

.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 0.4rem 0.6rem; text-align: left; border-bottom: 1px solid #2a2a2a; white-space: nowrap; }
th { color: #888; font-weight: 500; font-size: 0.85rem; }

.side-tag { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 3px; font-size: 0.8rem; font-weight: 500; }
.tag-buy { background: #14532d; color: #86efac; }
.tag-sell { background: #451a1a; color: #fca5a5; }

.exec-form { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 0.75rem; }
.form-field { display: flex; flex-direction: column; gap: 0.25rem; }
.form-field-wide { grid-column: 1 / -1; }
.form-field label { color: #aaa; font-size: 0.85rem; }
.form-field input,
.form-field select {
  background: #0f0f0f; color: #eee; border: 1px solid #333; border-radius: 4px; padding: 0.4rem 0.6rem;
}
.form-field select.side-buy { background: #14532d; color: #86efac; border-color: #10b981; }
.form-field select.side-sell { background: #451a1a; color: #fca5a5; border-color: #ef4444; }
.form-actions { margin-top: 0.75rem; }

.btn-primary { padding: 0.45rem 1.2rem; color: white; border: none; border-radius: 4px; cursor: pointer; background: #3b82f6; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-buy { background: #10b981; }
.btn-sell { background: #ef4444; }
.btn-secondary { padding: 0.4rem 1rem; background: #333; color: #ccc; border: 1px solid #444; border-radius: 4px; cursor: pointer; }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }

.metrics-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 0.75rem; }
.metric { display: flex; flex-direction: column; gap: 0.15rem; padding: 0.6rem; background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 4px; }
.metric-label { color: #888; font-size: 0.8rem; }
.metric-value { color: #eee; font-weight: 500; }
.metric-value.total { color: #3b82f6; font-size: 1.05rem; }
.drawdown-warn { color: #f59e0b; }

@media (max-width: 600px) {
  .exec-form { grid-template-columns: 1fr 1fr; }
  .form-field-wide { grid-column: 1 / -1; }
  .metrics-grid { grid-template-columns: 1fr 1fr; }
  th, td { padding: 0.3rem 0.4rem; font-size: 0.85rem; }
}
</style>
