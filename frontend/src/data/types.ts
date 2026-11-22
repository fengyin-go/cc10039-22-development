/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 同步字段变更单：目录字段差量文件落库后的可核对载体。 */
export type SyncFieldDiff = {
  field: string
  change: 'add' | 'drop' | 'type'
  from?: string
  to?: string
  result?: string
  note?: string
}

export type SyncTicket = {
  id: number
  ticket_no: string
  table: string
  status: string
  source_version: string
  input_base_version: number
  target_version_at_submit: number
  target_version_after_apply: number | null
  import_count: number
  applied_fields: string[]
  skipped_fields: string[]
  pending_review: string[]
  diffs: SyncFieldDiff[]
  created_at: string | null
  applied_at: string | null
}

export type SyncReceipt = {
  job_id: number
  job_name: string
  job_status: string
  tickets: SyncTicket[]
  totals: { tickets: number; applied_fields: number; pending_review_fields: number }
}

export type CatalogColumn = {
  field: string
  type: string
  version: number
  ticket_no: string
  pending_review: boolean
  review_from_ticket: string
}

export type CatalogSchema = {
  table: string
  version: number
  columns: CatalogColumn[]
}

export type PendingReviewItem = {
  table: string
  field: string
  type: string
  version: number
  review_from_ticket: string
}
