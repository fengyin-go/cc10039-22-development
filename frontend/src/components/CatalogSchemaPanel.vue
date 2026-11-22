<template>
  <section class="catalog-panel" data-testid="catalog-schema-panel">
    <h3>湖表生效结构</h3>
    <p class="panel-desc">
      读湖表结构时以目录生效的类型为准，每列可追溯到来源变更单；
      值班员也可在此独立调整某列类型（如同步批次下载文件后的目标端修改）。
    </p>

    <form class="schema-bar" @submit.prevent="loadSchema">
      <label>
        <span>湖表名</span>
        <input v-model="tableName" data-testid="schema-table" placeholder="如 ods_orders" required />
      </label>
      <button class="btn primary" type="submit" data-testid="schema-load">读取生效结构</button>
      <button class="btn ghost" type="button" data-testid="review-load" @click="loadPendingReview">
        刷新待核对范围
      </button>
    </form>

    <template v-if="schema">
      <p class="schema-version" data-testid="schema-version">
        湖表「{{ schema.table }}」当前目录版本：v{{ schema.version }}
      </p>
      <table class="data-table">
        <thead>
          <tr>
            <th>字段</th>
            <th>生效类型</th>
            <th>列版本</th>
            <th>来源变更单</th>
            <th>待核对</th>
            <th>独立调整类型</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="column in schema.columns" :key="column.field" :data-testid="`column-${column.field}`">
            <td>{{ column.field }}</td>
            <td><strong>{{ column.type }}</strong></td>
            <td>v{{ column.version }}</td>
            <td>{{ column.ticket_no }}</td>
            <td>
              <span v-if="column.pending_review" class="pending-text">
                待核对（{{ column.review_from_ticket }}）
              </span>
              <template v-else>—</template>
            </td>
            <td class="column-edit">
              <input
                v-model="editTypes[column.field]"
                :placeholder="column.type"
                :data-testid="`edit-${column.field}`"
              />
              <button
                class="link"
                type="button"
                :data-testid="`edit-submit-${column.field}`"
                @click="submitColumnEdit(column.field)"
              >
                调整
              </button>
            </td>
          </tr>
          <tr v-if="!schema.columns.length">
            <td colspan="6" class="empty-state">该湖表在目录中尚无字段定义</td>
          </tr>
        </tbody>
      </table>
    </template>

    <h4>待核对范围</h4>
    <table class="data-table">
      <thead>
        <tr>
          <th>湖表</th>
          <th>字段</th>
          <th>当前生效类型</th>
          <th>来源变更单</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in pendingReview" :key="`${item.table}.${item.field}`" data-testid="review-item">
          <td>{{ item.table }}</td>
          <td>{{ item.field }}</td>
          <td>{{ item.type }}</td>
          <td>{{ item.review_from_ticket }}</td>
        </tr>
        <tr v-if="!pendingReview.length">
          <td colspan="4" class="empty-state">当前没有待核对的字段冲突</td>
        </tr>
      </tbody>
    </table>

    <p v-if="message" class="panel-message" data-testid="catalog-message">{{ message }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="catalog-error">{{ errorMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  fetchPendingReview,
  fetchTableSchema,
  updateCatalogColumn,
} from '@/api/backend-service'
import type { CatalogSchema, PendingReviewItem } from '@/data/types'

const tableName = ref('ods_orders')
const schema = ref<CatalogSchema | null>(null)
const pendingReview = ref<PendingReviewItem[]>([])
const editTypes = ref<Record<string, string>>({})
const message = ref('')
const errorMessage = ref('')

async function loadSchema() {
  message.value = ''
  errorMessage.value = ''
  try {
    schema.value = await fetchTableSchema(tableName.value.trim())
    editTypes.value = {}
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '生效结构读取失败'
  }
}

async function loadPendingReview() {
  errorMessage.value = ''
  try {
    const result = await fetchPendingReview(tableName.value.trim() || undefined)
    pendingReview.value = result.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '待核对范围读取失败'
  }
}

async function submitColumnEdit(field: string) {
  message.value = ''
  errorMessage.value = ''
  const newType = (editTypes.value[field] ?? '').trim()
  if (!newType) {
    errorMessage.value = `请先填写 ${field} 的目标类型`
    return
  }
  try {
    const result = await updateCatalogColumn(tableName.value.trim(), field, newType)
    message.value = result.message
    await loadSchema()
    await loadPendingReview()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '目录列调整失败'
  }
}

onMounted(async () => {
  await loadSchema()
  await loadPendingReview()
})
</script>

<style scoped>
.catalog-panel {
  margin-top: 16px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
}
.catalog-panel h3 { margin: 0 0 4px; }
.catalog-panel h4 { margin: 16px 0 8px; }
.panel-desc { color: var(--muted); font-size: 12px; margin: 0 0 12px; }
.schema-bar {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  margin-bottom: 12px;
}
.schema-bar label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.schema-bar input {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.schema-version { font-size: 13px; color: var(--muted); }
.pending-text { color: #b42318; }
.column-edit { display: flex; gap: 6px; align-items: center; }
.column-edit input {
  width: 140px;
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
}
.panel-message { color: #067647; font-size: 13px; }
</style>
