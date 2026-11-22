<template>
  <section class="sync-panel" data-testid="sync-field-change-panel">
    <h3>同步字段变更单</h3>
    <p class="panel-desc">
      值班员上传源端目录字段差量文件（字段增删及类型差异），落库为变更单；
      数据目录在同步作业的回执内领取变更单并按字段应用确认过的差量。
    </p>

    <form class="batch-form" @submit.prevent="submitBatch">
      <label>
        <span>同步任务</span>
        <select v-model.number="selectedJobId" data-testid="batch-job">
          <option v-for="job in syncJobs" :key="job.id" :value="Number(job.id)">
            #{{ job.id }} {{ job['任务名称'] }}（{{ job.status }}）
          </option>
        </select>
      </label>
      <label>
        <span>目标湖表</span>
        <input v-model="batchForm.table" data-testid="batch-table" placeholder="如 ods_orders" required />
      </label>
      <label>
        <span>源端版本（本次输入版本）</span>
        <input v-model="batchForm.source_version" data-testid="batch-source-version" placeholder="如 src-2026-11-22" required />
      </label>
      <label>
        <span>文件基线版本（下载时目标端版本）</span>
        <input
          v-model.number="batchForm.base_version"
          data-testid="batch-base-version"
          type="number"
          min="0"
          required
        />
      </label>
      <label class="diff-input">
        <span>字段差量（JSON 数组：field / change=add|drop|type / from / to）</span>
        <textarea
          v-model="diffsText"
          data-testid="batch-diffs"
          rows="5"
          placeholder='[{"field":"order_id","change":"type","from":"string","to":"bigint"}]'
        ></textarea>
      </label>
      <div class="form-actions">
        <button class="btn" type="button" data-testid="batch-example" @click="fillExample">填入示例差量</button>
        <button class="btn primary" type="submit" data-testid="batch-submit" :disabled="submitting">
          {{ submitting ? '提交中…' : '提交同步批次' }}
        </button>
      </div>
    </form>

    <div class="receipt-head">
      <h4>同步作业应用回执</h4>
      <button class="btn ghost" type="button" data-testid="receipt-reload" @click="loadReceipt">刷新回执</button>
    </div>
    <p v-if="receipt" class="receipt-summary" data-testid="receipt-summary">
      作业「{{ receipt.job_name }}」状态 {{ receipt.job_status }}；变更单 {{ receipt.totals.tickets }} 张，
      已应用字段 {{ receipt.totals.applied_fields }} 个，待核对字段 {{ receipt.totals.pending_review_fields }} 个。
    </p>

    <table class="data-table">
      <thead>
        <tr>
          <th>变更单号</th>
          <th>目标湖表</th>
          <th>状态</th>
          <th>本次输入版本</th>
          <th>目标端版本</th>
          <th>已应用字段</th>
          <th>待核对字段</th>
          <th>投递次数</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="ticket in receipt?.tickets ?? []" :key="ticket.ticket_no" :data-testid="`ticket-${ticket.ticket_no}`">
          <td>{{ ticket.ticket_no }}</td>
          <td>{{ ticket.table }}</td>
          <td>{{ ticket.status }}</td>
          <td>{{ ticket.source_version }} @ v{{ ticket.input_base_version }}</td>
          <td>
            v{{ ticket.target_version_at_submit }}
            <template v-if="ticket.target_version_after_apply !== null">
              → v{{ ticket.target_version_after_apply }}
            </template>
          </td>
          <td>{{ ticket.applied_fields.join('、') || '—' }}</td>
          <td>
            <span v-if="ticket.pending_review.length" class="pending-text">
              {{ ticket.pending_review.join('、') }}
            </span>
            <template v-else>—</template>
          </td>
          <td>{{ ticket.import_count }}</td>
          <td>
            <button
              v-if="ticket.status === '待应用'"
              class="link"
              type="button"
              :data-testid="`apply-${ticket.ticket_no}`"
              @click="applyTicket(ticket.id)"
            >
              领取并应用
            </button>
            <span v-else class="muted">已领取</span>
          </td>
        </tr>
        <tr v-if="!receipt?.tickets?.length">
          <td colspan="9" class="empty-state">暂无变更单，请先提交同步批次</td>
        </tr>
      </tbody>
    </table>

    <ul v-if="lastTicket" class="diff-result" data-testid="diff-result">
      <li v-for="diff in lastTicket.diffs" :key="`${lastTicket.ticket_no}-${diff.field}`">
        <strong>{{ diff.field }}</strong>（{{ diff.change }}<template v-if="diff.to"> → {{ diff.to }}</template>）：
        {{ diff.result }}<span v-if="diff.note" class="muted"> — {{ diff.note }}</span>
      </li>
    </ul>

    <p v-if="message" class="panel-message" data-testid="panel-message">{{ message }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="panel-error">{{ errorMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  applySyncTicket,
  fetchSyncReceipt,
  listEntries,
  submitSyncBatch,
} from '@/api/backend-service'
import type { EntryRow, SyncReceipt, SyncTicket } from '@/data/types'

const syncJobs = ref<EntryRow[]>([])
const selectedJobId = ref(1)
const receipt = ref<SyncReceipt | null>(null)
const lastTicket = ref<SyncTicket | null>(null)
const submitting = ref(false)
const message = ref('')
const errorMessage = ref('')

const batchForm = ref({
  table: 'ods_orders',
  source_version: '',
  base_version: 1,
})
const diffsText = ref('')

function fillExample() {
  batchForm.value = { table: 'ods_orders', source_version: 'src-2026-11-22', base_version: 1 }
  diffsText.value = JSON.stringify(
    [
      { field: 'order_id', change: 'type', from: 'string', to: 'bigint' },
      { field: 'discount', change: 'add', to: 'decimal(10,2)' },
    ],
    null,
    2,
  )
}

async function submitBatch() {
  submitting.value = true
  message.value = ''
  errorMessage.value = ''
  try {
    let diffs: unknown
    try {
      diffs = JSON.parse(diffsText.value)
    } catch {
      throw new Error('字段差量不是合法的 JSON')
    }
    if (!Array.isArray(diffs)) throw new Error('字段差量必须是 JSON 数组')
    const result = await submitSyncBatch(selectedJobId.value, {
      table: batchForm.value.table,
      source_version: batchForm.value.source_version,
      base_version: batchForm.value.base_version,
      diffs: diffs as never,
    })
    lastTicket.value = result.ticket
    message.value = result.message
    await loadReceipt()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '同步批次提交失败'
  } finally {
    submitting.value = false
  }
}

async function applyTicket(ticketId: number) {
  message.value = ''
  errorMessage.value = ''
  try {
    const result = await applySyncTicket(ticketId)
    lastTicket.value = result.ticket
    message.value = result.message
    await loadReceipt()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '变更单领取失败'
  }
}

async function loadReceipt() {
  errorMessage.value = ''
  try {
    receipt.value = await fetchSyncReceipt(selectedJobId.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回执读取失败'
  }
}

onMounted(async () => {
  try {
    const payload = await listEntries('metadata_sync')
    syncJobs.value = payload.items
    if (payload.items.length) selectedJobId.value = Number(payload.items[0].id)
    await loadReceipt()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '同步任务读取失败'
  }
})
</script>

<style scoped>
.sync-panel {
  margin-top: 16px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
}
.sync-panel h3 { margin: 0 0 4px; }
.panel-desc { color: var(--muted); font-size: 12px; margin: 0 0 12px; }
.batch-form {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  margin-bottom: 16px;
  padding: 12px;
  background: #fafbfc;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.batch-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.batch-form input, .batch-form select, .batch-form textarea {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.diff-input { flex: 1 1 100%; }
.diff-input textarea { width: 100%; font-family: monospace; box-sizing: border-box; }
.form-actions { display: flex; gap: 8px; }
.receipt-head { display: flex; justify-content: space-between; align-items: center; }
.receipt-head h4 { margin: 0; }
.receipt-summary { font-size: 13px; color: var(--muted); }
.pending-text { color: #b42318; }
.muted { color: var(--muted); font-size: 12px; }
.diff-result { font-size: 13px; margin: 12px 0 0; padding-left: 18px; }
.panel-message { color: #067647; font-size: 13px; }
</style>
