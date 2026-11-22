"""元数据同步领域服务：管理湖内元数据与外部元数据源的同步。

新增"同步字段变更单"：值班员向同步作业提交源端的目录字段差量文件
（字段增删及类型差异），批次落库为变更单；数据目录在同步作业的回执内
领取变更单并按字段应用确认过的差量。同一差量文件重复导入只记一次；
变更单同时记录本次输入版本（源端版本 + 文件基线版本）与目标端版本
（提交时 / 应用后的目录版本），供值班员核对。
"""
from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any

from app.services.base import BaseModuleService
from app.services.data_catalog import DIFF_CHANGE_TYPES, DataCatalogService
from app.store import store

# 变更单状态
TICKET_PENDING = "待应用"
TICKET_APPLIED = "已应用"
TICKET_PARTIAL = "部分应用"


class MetadataSyncService(BaseModuleService):
    MODULE = "metadata_sync"
    ENTITY = "同步任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["任务名称", "源端类型", "目标端类型", "同步频率", "最后同步时间"]
    REQUIRED_FIELDS = ["任务名称", "源端类型", "目标端类型"]
    STATUS_ORDER = ["待同步", "同步中", "同步成功", "同步失败"]
    ACTION_RULES = {"手动同步": "同步中", "跳过同步": "同步失败"}
    NEGATIVE_ACTIONS = []

    # ---- 提交同步批次：差量文件落库为变更单 ----

    def submit_batch(
        self, job_id: int, payload: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """上传目录字段差量文件，生成同步字段变更单。

        返回 (变更单, 消息, 是否重复投递)。同一差量文件（按内容指纹）在同一
        同步作业下重复导入只记一次，直接返回首次落库的变更单。
        """
        job = store.find(self.MODULE, job_id)
        if job is None:
            return None, f"同步任务 {job_id} 不存在或已归档", False

        error = self._validate_diff_file(payload)
        if error:
            return None, error, False

        table = str(payload["table"]).strip()
        source_version = str(payload["source_version"]).strip()
        base_version = int(payload["base_version"])
        diffs = [
            {
                "field": str(item["field"]).strip(),
                "change": str(item["change"]).strip(),
                "from": str(item.get("from") or "").strip(),
                "to": str(item.get("to") or "").strip(),
            }
            for item in payload["diffs"]
        ]

        fingerprint = self._fingerprint(table, source_version, base_version, diffs)
        existing = store.find_sync_ticket_by_fingerprint(job_id, fingerprint)
        if existing is not None:
            existing["import_count"] = int(existing.get("import_count", 1)) + 1
            return (
                existing,
                f"同一差量文件已导入过（变更单 {existing['ticket_no']}），本次重复投递不再落库",
                True,
            )

        ticket_id = store.next_sync_ticket_id()
        ticket = {
            "id": ticket_id,
            "ticket_no": f"SFC-{ticket_id:04d}",
            "job_id": job_id,
            "table": table,
            "source_version": source_version,
            "input_base_version": base_version,
            "target_version_at_submit": store.catalog_table_version(table),
            "target_version_after_apply": None,
            "file_fingerprint": fingerprint,
            "status": TICKET_PENDING,
            "diffs": [dict(item, result="待应用", note="") for item in diffs],
            "applied_fields": [],
            "skipped_fields": [],
            "pending_review": [],
            "import_count": 1,
            "created_at": datetime.datetime.now().isoformat(),
            "applied_at": None,
        }
        store.add_sync_ticket(ticket)

        job["status"] = "同步中"
        job["pending"] = True
        job["最后同步时间"] = datetime.date.today().isoformat()

        store.log_event("sync_batch_submitted", "metadata_sync", "data_catalog", {
            "job_id": job_id,
            "ticket_no": ticket["ticket_no"],
            "table": table,
            "source_version": source_version,
            "input_base_version": base_version,
            "target_version_at_submit": ticket["target_version_at_submit"],
            "diff_fields": [item["field"] for item in diffs],
        })
        return (
            ticket,
            f"同步字段变更单 {ticket['ticket_no']} 已登记（{len(diffs)} 个字段差量），等待数据目录领取",
            False,
        )

    # ---- 数据目录领取变更单并应用 ----

    def apply_ticket(self, ticket_id: int) -> tuple[dict[str, Any] | None, str]:
        """数据目录从同步作业回执领取变更单，按字段应用确认过的差量。"""
        ticket = store.find_sync_ticket(ticket_id)
        if ticket is None:
            return None, f"同步字段变更单 {ticket_id} 不存在"
        if ticket.get("status") != TICKET_PENDING:
            return ticket, f"变更单 {ticket['ticket_no']} 已应用过，应用结果保持不变"

        table = ticket["table"]
        catalog = DataCatalogService()
        outcome = catalog.apply_field_diffs(
            table, ticket["diffs"], int(ticket["input_base_version"]), ticket["ticket_no"]
        )

        ticket["diffs"] = outcome["diffs"]
        ticket["applied_fields"] = outcome["applied"]
        ticket["skipped_fields"] = outcome["skipped"]
        ticket["pending_review"] = outcome["pending_review"]
        ticket["status"] = TICKET_PARTIAL if outcome["pending_review"] else TICKET_APPLIED
        ticket["target_version_after_apply"] = store.catalog_table_version(table)
        ticket["applied_at"] = datetime.datetime.now().isoformat()

        job = store.find(self.MODULE, int(ticket["job_id"]))
        if job is not None:
            job["status"] = "同步成功"
            job["pending"] = False
            job["abnormal"] = False

        store.log_event("sync_ticket_applied", "metadata_sync", "data_catalog", {
            "job_id": ticket["job_id"],
            "ticket_no": ticket["ticket_no"],
            "table": table,
            "applied_fields": outcome["applied"],
            "pending_review": outcome["pending_review"],
            "target_version_after_apply": ticket["target_version_after_apply"],
        })

        if outcome["pending_review"]:
            return ticket, (
                f"变更单 {ticket['ticket_no']} 部分应用：生效 {len(outcome['applied'])} 个字段，"
                f"{len(outcome['pending_review'])} 个字段与目标端冲突待核对"
            )
        return ticket, f"变更单 {ticket['ticket_no']} 已应用：生效 {len(outcome['applied'])} 个字段"

    # ---- 查询：变更单与同步作业回执 ----

    def get_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        return store.find_sync_ticket(ticket_id)

    def sync_receipt(self, job_id: int) -> dict[str, Any] | None:
        """同步作业应用回执：变更单列表、应用了哪些字段、待核对范围与版本核对信息。"""
        job = store.find(self.MODULE, job_id)
        if job is None:
            return None
        tickets = store.sync_tickets_for_job(job_id)
        return {
            "job_id": job_id,
            "job_name": job.get("任务名称", ""),
            "job_status": job.get("status", ""),
            "tickets": [self._ticket_view(ticket) for ticket in tickets],
            "totals": {
                "tickets": len(tickets),
                "applied_fields": sum(len(t.get("applied_fields", [])) for t in tickets),
                "pending_review_fields": sum(len(t.get("pending_review", [])) for t in tickets),
            },
        }

    def _ticket_view(self, ticket: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": ticket["id"],
            "ticket_no": ticket["ticket_no"],
            "table": ticket["table"],
            "status": ticket["status"],
            "source_version": ticket["source_version"],
            "input_base_version": ticket["input_base_version"],
            "target_version_at_submit": ticket["target_version_at_submit"],
            "target_version_after_apply": ticket["target_version_after_apply"],
            "import_count": ticket.get("import_count", 1),
            "applied_fields": ticket.get("applied_fields", []),
            "skipped_fields": ticket.get("skipped_fields", []),
            "pending_review": ticket.get("pending_review", []),
            "diffs": ticket.get("diffs", []),
            "created_at": ticket.get("created_at"),
            "applied_at": ticket.get("applied_at"),
        }

    # ---- 差量文件校验与指纹 ----

    @staticmethod
    def _validate_diff_file(payload: dict[str, Any]) -> str:
        table = str(payload.get("table") or "").strip()
        if not table:
            return "差量文件缺少目标湖表 table"
        if not str(payload.get("source_version") or "").strip():
            return "差量文件缺少源端版本 source_version"
        base_version = payload.get("base_version")
        if not isinstance(base_version, int) or isinstance(base_version, bool) or base_version < 0:
            return "差量文件缺少合法的文件基线版本 base_version（目标端版本号）"
        diffs = payload.get("diffs")
        if not isinstance(diffs, list) or not diffs:
            return "差量文件缺少字段差量列表 diffs"
        for item in diffs:
            if not isinstance(item, dict):
                return "字段差量条目必须是对象"
            if not str(item.get("field") or "").strip():
                return "字段差量条目缺少 field"
            change = str(item.get("change") or "").strip()
            if change not in DIFF_CHANGE_TYPES:
                return f"字段 {item.get('field')} 的变更动作 {change or '空'} 不合法，仅支持 add/drop/type"
            if change in ("add", "type") and not str(item.get("to") or "").strip():
                return f"字段 {item.get('field')} 的 {change} 变更缺少目标类型 to"
        return ""

    @staticmethod
    def _fingerprint(
        table: str, source_version: str, base_version: int, diffs: list[dict[str, Any]]
    ) -> str:
        canonical = json.dumps(
            {
                "table": table,
                "source_version": source_version,
                "base_version": base_version,
                "diffs": sorted(diffs, key=lambda item: (item["field"], item["change"])),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
