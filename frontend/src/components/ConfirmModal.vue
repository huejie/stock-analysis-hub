<script setup lang="ts">
import { ref } from 'vue'
import type { StockRecord } from '../types'

const props = defineProps<{
  data: { date: string; records: StockRecord[] }
}>()

const emit = defineEmits<{
  save: [data: { date: string; records: StockRecord[] }]
  cancel: []
}>()

const date = ref(props.data.date)
const editedRecords = ref(props.data.records.map(r => ({ ...r })))

function updateField(i: number, field: keyof StockRecord, value: string) {
  const rec = editedRecords.value[i]!
  if (['heat_value', 'price_change_pct', 'turnover_amount', 'holders_today', 'holders_yesterday'].includes(field)) {
    (rec as Record<string, unknown>)[field] = parseFloat(value) || 0
  } else {
    (rec as Record<string, unknown>)[field] = value
  }
}
</script>

<template>
  <div class="modal-overlay show">
    <div class="modal">
      <div class="modal-header">
        <h2>确认识别结果</h2>
        <span class="modal-count">识别到 {{ editedRecords.length }} 只股票</span>
      </div>
      <div class="modal-date-row">
        <label>数据日期</label>
        <input v-model="date" type="date">
      </div>
      <p class="modal-hint">请检查以下数据是否正确，可点击单元格编辑</p>
      <div class="table-wrap">
        <table class="edit-table">
          <thead>
            <tr>
              <th>#</th><th>股票名称</th><th>代码</th><th>热度(万)</th>
              <th>涨跌%</th><th>成交(亿)</th><th>今持仓</th><th>昨持仓</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in editedRecords" :key="i">
              <td><strong>{{ r.rank }}</strong></td>
              <td><input :value="r.stock_name" @change="updateField(i, 'stock_name', ($event.target as HTMLInputElement).value)"></td>
              <td><input :value="r.stock_code" maxlength="6" @change="updateField(i, 'stock_code', ($event.target as HTMLInputElement).value)"></td>
              <td><input type="number" :value="r.heat_value" step="0.01" @change="updateField(i, 'heat_value', ($event.target as HTMLInputElement).value)"></td>
              <td><input type="number" :value="r.price_change_pct" step="0.1" @change="updateField(i, 'price_change_pct', ($event.target as HTMLInputElement).value)"></td>
              <td><input type="number" :value="r.turnover_amount" step="0.1" @change="updateField(i, 'turnover_amount', ($event.target as HTMLInputElement).value)"></td>
              <td><input type="number" :value="r.holders_today" @change="updateField(i, 'holders_today', ($event.target as HTMLInputElement).value)"></td>
              <td><input type="number" :value="r.holders_yesterday" @change="updateField(i, 'holders_yesterday', ($event.target as HTMLInputElement).value)"></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="actions">
        <button class="btn btn-outline" @click="$emit('cancel')">取消</button>
        <button class="btn btn-primary" @click="$emit('save', { date, records: editedRecords })">确认保存</button>
      </div>
    </div>
  </div>
</template>
