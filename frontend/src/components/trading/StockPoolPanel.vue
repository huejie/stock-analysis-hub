<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useTradingApi } from '../../composables/useTradingApi'
import type { StockPoolListItem, StockPoolVersion } from '../../types/trading'

const api = useTradingApi()
const versions = ref<StockPoolListItem[]>([])
const selected = ref<StockPoolVersion | null>(null)
const inputText = ref('')
const inputSource = ref<'text' | 'csv'>('text')
const loading = ref(false)
const error = ref('')
const success = ref('')

// placeholder 用 computed 避免模板内多行字符串 + 字符实体的解析问题
const placeholderText = computed(() =>
  inputSource.value === 'text'
    ? '每行一只: 000001 平安银行\n600000 浦发银行\n300750'
    : 'code,name\n000001,平安银行\n600000,浦发银行'
)

async function loadVersions() {
  try {
    const res = await api.listStockPools()
    versions.value = res.versions
    const first = versions.value[0]
    if (first && !selected.value) {
      await selectVersion(first.id)
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function selectVersion(id: number) {
  try {
    selected.value = await api.getStockPool(id)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function handleImport() {
  if (!inputText.value.trim()) {
    error.value = '请输入股票代码'
    return
  }
  loading.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await api.importStockPool({
      pool_name: 'default',
      source: inputSource.value,
      text_body: inputText.value,
    })
    success.value = result.reused
      ? `股票池未变化(版本 ${result.version_no})`
      : `导入成功:版本 ${result.version_no},共 ${result.items_count} 只`
    inputText.value = ''
    await loadVersions()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadVersions)
</script>

<template>
  <div class="panel">
    <h3>股票池管理</h3>

    <!-- 导入区 -->
    <div class="import-section">
      <div class="source-tabs">
        <button :class="{ active: inputSource === 'text' }" @click="inputSource = 'text'">文本</button>
        <button :class="{ active: inputSource === 'csv' }" @click="inputSource = 'csv'">CSV</button>
      </div>
      <textarea
        v-model="inputText"
        :placeholder="placeholderText"
        rows="6"
      />
      <button class="btn-primary" :disabled="loading" @click="handleImport">
        {{ loading ? '导入中...' : '导入股票池' }}
      </button>
    </div>

    <p v-if="error" class="msg error">{{ error }}</p>
    <p v-if="success" class="msg success">{{ success }}</p>

    <!-- 版本列表 -->
    <div class="versions" v-if="versions.length">
      <h4>历史版本</h4>
      <div class="version-list">
        <button
          v-for="v in versions"
          :key="v.id"
          class="version-item"
          :class="{ active: selected?.id === v.id }"
          @click="selectVersion(v.id)"
        >
          v{{ v.version_no }} · {{ v.items_count }} 只 · {{ v.created_at.slice(0, 16) }}
        </button>
      </div>
    </div>

    <!-- 版本明细 -->
    <div class="items" v-if="selected">
      <h4>版本 v{{ selected.version_no }} 明细</h4>
      <table>
        <thead>
          <tr><th>代码</th><th>名称</th><th>板块</th><th>黑名单</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in selected.items" :key="item.stock_code">
            <td>{{ item.stock_code }}</td>
            <td>{{ item.stock_name || '-' }}</td>
            <td>{{ item.sector_name || '-' }}</td>
            <td>{{ item.manual_blacklist ? '是' : '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.panel { padding: 1rem; }
.import-section { display: flex; flex-direction: column; gap: 0.5rem; margin-bottom: 1rem; }
.source-tabs { display: flex; gap: 0.5rem; }
.source-tabs button { padding: 0.25rem 0.75rem; border: 1px solid #444; background: transparent; color: #ccc; border-radius: 4px; cursor: pointer; }
.source-tabs button.active { background: #3b82f6; color: white; border-color: #3b82f6; }
textarea { width: 100%; background: #1a1a1a; color: #eee; border: 1px solid #333; border-radius: 4px; padding: 0.5rem; font-family: monospace; resize: vertical; }
.btn-primary { padding: 0.5rem 1rem; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; align-self: flex-start; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.msg { padding: 0.5rem; border-radius: 4px; }
.msg.error { background: #451a1a; color: #fca5a5; }
.msg.success { background: #14532d; color: #86efac; }
.version-list { display: flex; flex-direction: column; gap: 0.25rem; }
.version-item { text-align: left; padding: 0.5rem; background: #1a1a1a; color: #ccc; border: 1px solid #333; border-radius: 4px; cursor: pointer; }
.version-item.active { border-color: #3b82f6; background: #1e3a5f; }
table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
th, td { padding: 0.4rem 0.6rem; text-align: left; border-bottom: 1px solid #2a2a2a; }
th { color: #888; font-weight: 500; }
</style>
