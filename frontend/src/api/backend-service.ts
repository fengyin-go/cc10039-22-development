import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  CatalogSchema,
  EntryRow,
  OverviewResult,
  PageResult,
  PendingReviewItem,
  SyncFieldDiff,
  SyncReceipt,
  SyncTicket,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

/* ---- 同步字段变更单（元数据同步 → 数据目录） ---- */

export type SyncBatchPayload = {
  table: string
  source_version: string
  base_version: number
  diffs: SyncFieldDiff[]
}

export async function submitSyncBatch(
  jobId: number,
  payload: SyncBatchPayload,
): Promise<{ ok: boolean; duplicated: boolean; message: string; ticket: SyncTicket }> {
  const response = await request(`/api/metadata-sync/${jobId}/batches`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const body = await response.json()
  if (!response.ok) throw new Error(body?.detail ?? '同步批次提交失败')
  return body
}

export async function fetchSyncReceipt(jobId: number): Promise<SyncReceipt> {
  const response = await request(`/api/metadata-sync/${jobId}/receipt`)
  if (!response.ok) throw new Error('同步作业回执读取失败')
  return await response.json()
}

export async function applySyncTicket(
  ticketId: number,
): Promise<{ ok: boolean; message: string; ticket: SyncTicket }> {
  const response = await request(`/api/metadata-sync/tickets/${ticketId}/apply`, {
    method: 'POST',
  })
  const body = await response.json()
  if (!response.ok) throw new Error(body?.detail ?? '变更单领取失败')
  return body
}

export async function fetchTableSchema(table: string): Promise<CatalogSchema> {
  const response = await request(`/api/data-catalog/tables/${encodeURIComponent(table)}/schema`)
  if (!response.ok) throw new Error('湖表生效结构读取失败')
  return await response.json()
}

export async function updateCatalogColumn(
  table: string,
  field: string,
  type: string,
): Promise<{ ok: boolean; message: string }> {
  const response = await request(
    `/api/data-catalog/tables/${encodeURIComponent(table)}/columns/${encodeURIComponent(field)}`,
    { method: 'POST', body: JSON.stringify({ type }) },
  )
  const body = await response.json()
  if (!response.ok) throw new Error(body?.detail ?? '目录列调整失败')
  return body
}

export async function fetchPendingReview(
  table?: string,
): Promise<{ total: number; items: PendingReviewItem[] }> {
  const query = table ? `?table=${encodeURIComponent(table)}` : ''
  const response = await request(`/api/data-catalog/pending-review${query}`)
  if (!response.ok) throw new Error('待核对范围读取失败')
  return await response.json()
}
