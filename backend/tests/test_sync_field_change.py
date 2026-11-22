"""同步字段变更单领域测试：差量文件落库、目录领取应用、冲突裁决与幂等。

覆盖需求：
- 值班员上传源端增删及类型差异，落库为同步字段变更单；
- 数据目录在同步作业回执内领取变更单，按字段应用确认过的差量；
- 读湖表结构以目录生效类型为准，并可追溯到来源变更单；
- 目标列在文件下载后被独立修改且与导入类型冲突时，保留目标端、列入待核对；
- 文件以外的字段不受此轮变更影响；
- 同一差量文件重复导入只记一次；
- 目标端版本和本次输入版本都能从变更单核对。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.data_catalog import DataCatalogService
from app.services.metadata_sync import MetadataSyncService
from app.store import store

# 各测试用例使用独立湖表，避免共享内存仓库互相干扰
TABLE_BASIC = "ods_sync_basic"
TABLE_CONFLICT = "ods_sync_conflict"
TABLE_FLOW = "ods_sync_flow"


def _seed_table(table: str, columns: dict[str, str]) -> None:
    """重置并初始化一张目录表：列版本统一从 1 递增，来源记为 SEED。"""
    store._catalog_fields[table] = {}
    store._catalog_versions[table] = 0
    for field, col_type in columns.items():
        store.bump_catalog_field(table, field, col_type, "SEED")


def _diff_file(table: str, source_version: str, base_version: int, diffs: list[dict]) -> dict:
    return {
        "table": table,
        "source_version": source_version,
        "base_version": base_version,
        "diffs": diffs,
    }


class TestSubmitBatch:
    """提交同步批次：差量文件落库为变更单，双端版本可核对。"""

    def setup_method(self):
        self.sync_svc = MetadataSyncService()
        self.catalog_svc = DataCatalogService()
        _seed_table(TABLE_BASIC, {
            "order_id": "string",
            "user_id": "bigint",
            "amount": "decimal(10,2)",
        })

    def test_submit_creates_ticket_with_both_versions(self):
        base_version = store.catalog_table_version(TABLE_BASIC)
        payload = _diff_file(TABLE_BASIC, "src-v12", base_version, [
            {"field": "order_id", "change": "type", "from": "string", "to": "bigint"},
            {"field": "discount", "change": "add", "to": "decimal(10,2)"},
        ])
        ticket, message, duplicated = self.sync_svc.submit_batch(1, payload)

        assert ticket is not None, message
        assert duplicated is False
        assert ticket["status"] == "待应用"
        # 本次输入版本：源端版本 + 文件基线版本
        assert ticket["source_version"] == "src-v12"
        assert ticket["input_base_version"] == base_version
        # 目标端版本：提交时的目录版本，应用前为空
        assert ticket["target_version_at_submit"] == base_version
        assert ticket["target_version_after_apply"] is None
        assert ticket["ticket_no"].startswith("SFC-")

    def test_duplicate_batch_recorded_only_once(self):
        base_version = store.catalog_table_version(TABLE_BASIC)
        payload = _diff_file(TABLE_BASIC, "src-v12", base_version, [
            {"field": "discount", "change": "add", "to": "decimal(10,2)"},
        ])
        first, _, dup1 = self.sync_svc.submit_batch(1, payload)
        before = len(store._sync_tickets)
        second, message, dup2 = self.sync_svc.submit_batch(1, payload)

        assert dup1 is False
        assert dup2 is True
        assert len(store._sync_tickets) == before, "重复投递不应新增落库行"
        assert second["ticket_no"] == first["ticket_no"], "重复投递应返回首次的变更单"
        assert second["import_count"] == 2

    def test_invalid_diff_file_rejected(self):
        ticket, message, _ = self.sync_svc.submit_batch(1, {
            "table": TABLE_BASIC,
            "source_version": "src-v12",
            "base_version": 0,
            "diffs": [{"field": "x", "change": "rename"}],
        })
        assert ticket is None
        assert "不合法" in message

        ticket, message, _ = self.sync_svc.submit_batch(1, {
            "table": TABLE_BASIC,
            "source_version": "src-v12",
            "base_version": 0,
            "diffs": [{"field": "x", "change": "add"}],
        })
        assert ticket is None
        assert "缺少目标类型" in message

    def test_submit_unknown_job_rejected(self):
        ticket, message, _ = self.sync_svc.submit_batch(99999, _diff_file(
            TABLE_BASIC, "src-v12", 0,
            [{"field": "discount", "change": "add", "to": "decimal(10,2)"}],
        ))
        assert ticket is None
        assert "不存在" in message


class TestApplyTicket:
    """数据目录领取变更单：按字段应用，读结构以目录生效类型为准。"""

    def setup_method(self):
        self.sync_svc = MetadataSyncService()
        self.catalog_svc = DataCatalogService()
        _seed_table(TABLE_CONFLICT, {
            "order_id": "string",
            "user_id": "bigint",
            "amount": "decimal(10,2)",
            "legacy_flag": "string",
        })

    def _submit(self, diffs: list[dict], source_version: str = "src-v20"):
        base_version = store.catalog_table_version(TABLE_CONFLICT)
        payload = _diff_file(TABLE_CONFLICT, source_version, base_version, diffs)
        ticket, message, duplicated = self.sync_svc.submit_batch(1, payload)
        assert ticket is not None and not duplicated, message
        return ticket

    def test_apply_updates_effective_schema_and_traceable(self):
        ticket = self._submit([
            {"field": "order_id", "change": "type", "from": "string", "to": "bigint"},
            {"field": "discount", "change": "add", "to": "decimal(10,2)"},
            {"field": "legacy_flag", "change": "drop"},
        ])
        applied_ticket, message = self.sync_svc.apply_ticket(ticket["id"])

        assert applied_ticket["status"] == "已应用", message
        assert set(applied_ticket["applied_fields"]) == {"order_id", "discount", "legacy_flag"}
        assert applied_ticket["pending_review"] == []
        assert applied_ticket["target_version_after_apply"] is not None
        assert applied_ticket["target_version_after_apply"] > applied_ticket["target_version_at_submit"]

        schema = self.catalog_svc.effective_schema(TABLE_CONFLICT)
        columns = {col["field"]: col for col in schema["columns"]}
        # 读湖表结构以目录生效类型为准
        assert columns["order_id"]["type"] == "bigint"
        assert columns["discount"]["type"] == "decimal(10,2)"
        assert "legacy_flag" not in columns
        # 可追溯到来源变更单
        assert columns["order_id"]["ticket_no"] == ticket["ticket_no"]
        assert columns["discount"]["ticket_no"] == ticket["ticket_no"]
        # 文件以外的字段不受此轮变更影响
        assert columns["user_id"]["type"] == "bigint"
        assert columns["amount"]["type"] == "decimal(10,2)"
        assert columns["user_id"]["ticket_no"] == "SEED"

    def test_apply_is_idempotent(self):
        ticket = self._submit([
            {"field": "discount", "change": "add", "to": "decimal(10,2)"},
        ])
        first, _ = self.sync_svc.apply_ticket(ticket["id"])
        version_after_first = store.catalog_table_version(TABLE_CONFLICT)
        second, message = self.sync_svc.apply_ticket(ticket["id"])

        assert "保持不变" in message
        assert second["status"] == first["status"]
        assert store.catalog_table_version(TABLE_CONFLICT) == version_after_first

    def test_conflict_keeps_target_and_enters_pending_review(self):
        # 先提交批次（下载文件时的基线版本已记录），再独立修改目标列制造并发冲突
        ticket = self._submit([
            {"field": "order_id", "change": "type", "from": "string", "to": "bigint"},
            {"field": "discount", "change": "add", "to": "decimal(10,2)"},
        ])
        column, message = self.catalog_svc.update_column_type(TABLE_CONFLICT, "order_id", "varchar(64)")
        assert column is not None, message

        applied_ticket, message = self.sync_svc.apply_ticket(ticket["id"])

        assert applied_ticket["status"] == "部分应用"
        assert applied_ticket["applied_fields"] == ["discount"]
        assert applied_ticket["pending_review"] == ["order_id"]

        schema = self.catalog_svc.effective_schema(TABLE_CONFLICT)
        columns = {col["field"]: col for col in schema["columns"]}
        # 冲突列保留目标端独立修改后的类型；非冲突列按导入生效
        assert columns["order_id"]["type"] == "varchar(64)"
        assert columns["order_id"]["pending_review"] is True
        assert columns["order_id"]["review_from_ticket"] == ticket["ticket_no"]
        assert columns["discount"]["type"] == "decimal(10,2)"

        scope = self.catalog_svc.pending_review_scope(TABLE_CONFLICT)
        assert [item["field"] for item in scope] == ["order_id"]
        assert scope[0]["review_from_ticket"] == ticket["ticket_no"]

    def test_manual_update_clears_pending_review(self):
        ticket = self._submit([
            {"field": "order_id", "change": "type", "from": "string", "to": "bigint"},
        ])
        self.catalog_svc.update_column_type(TABLE_CONFLICT, "order_id", "varchar(64)")
        self.sync_svc.apply_ticket(ticket["id"])
        assert len(self.catalog_svc.pending_review_scope(TABLE_CONFLICT)) == 1

        # 值班员人工核对后独立调整，待核对标记清除
        self.catalog_svc.update_column_type(TABLE_CONFLICT, "order_id", "bigint")
        assert self.catalog_svc.pending_review_scope(TABLE_CONFLICT) == []
        schema = self.catalog_svc.effective_schema(TABLE_CONFLICT)
        columns = {col["field"]: col for col in schema["columns"]}
        assert columns["order_id"]["type"] == "bigint"
        assert columns["order_id"]["ticket_no"] == "MANUAL"


class TestSyncReceiptAndAcceptanceFlow:
    """验收流程：两处源字段变化 → 独立改目标一列 → 回执与可读类型 → 重复投递核对。"""

    def setup_method(self):
        self.sync_svc = MetadataSyncService()
        self.catalog_svc = DataCatalogService()
        _seed_table(TABLE_FLOW, {
            "order_id": "string",
            "user_id": "bigint",
            "amount": "decimal(10,2)",
        })

    def test_acceptance_flow(self):
        # 1) 提交同步批次前，源端两处字段变化（类型变更 + 新增字段）
        base_version = store.catalog_table_version(TABLE_FLOW)
        payload = _diff_file(TABLE_FLOW, "src-v30", base_version, [
            {"field": "order_id", "change": "type", "from": "string", "to": "bigint"},
            {"field": "discount", "change": "add", "to": "decimal(10,2)"},
        ])

        # 2) 目标目录其中一列被独立修改（文件下载后的并发变更）
        column, message = self.catalog_svc.update_column_type(TABLE_FLOW, "order_id", "varchar(64)")
        assert column is not None, message

        # 3) 提交同步批次：变更单记录本次输入版本与目标端版本
        ticket, message, duplicated = self.sync_svc.submit_batch(1, payload)
        assert ticket is not None and not duplicated, message
        assert ticket["input_base_version"] == base_version
        assert ticket["target_version_at_submit"] == store.catalog_table_version(TABLE_FLOW)
        assert ticket["target_version_at_submit"] > ticket["input_base_version"], \
            "目标端已独立演进，提交时版本应高于文件基线版本"

        # 4) 数据目录领取并应用
        applied_ticket, message = self.sync_svc.apply_ticket(ticket["id"])
        assert applied_ticket["status"] == "部分应用", message

        # 5) 查看同步作业应用回执
        receipt = self.sync_svc.sync_receipt(1)
        assert receipt is not None
        receipt_ticket = next(t for t in receipt["tickets"] if t["ticket_no"] == ticket["ticket_no"])
        assert receipt_ticket["applied_fields"] == ["discount"]
        assert receipt_ticket["pending_review"] == ["order_id"]
        assert receipt_ticket["source_version"] == "src-v30"
        assert receipt_ticket["input_base_version"] == base_version
        assert receipt_ticket["target_version_after_apply"] == store.catalog_table_version(TABLE_FLOW)

        # 6) 两列实际可读类型：冲突列保留目标端，新增列按导入生效
        schema = self.catalog_svc.effective_schema(TABLE_FLOW)
        columns = {col["field"]: col for col in schema["columns"]}
        assert columns["order_id"]["type"] == "varchar(64)"
        assert columns["discount"]["type"] == "decimal(10,2)"
        # 文件以外字段不受影响
        assert columns["user_id"]["type"] == "bigint"
        assert columns["amount"]["type"] == "decimal(10,2)"

        # 7) 再投递同一批次：落库行数与待核对范围不变
        tickets_before = len(store._sync_tickets)
        scope_before = self.catalog_svc.pending_review_scope(TABLE_FLOW)
        again, message, duplicated = self.sync_svc.submit_batch(1, payload)
        assert duplicated is True
        assert again["ticket_no"] == ticket["ticket_no"]
        assert len(store._sync_tickets) == tickets_before, "重复投递不应新增落库行"
        assert self.catalog_svc.pending_review_scope(TABLE_FLOW) == scope_before, \
            "重复投递后待核对范围应保持不变"

        # 回执汇总仍只记一次
        receipt_after = self.sync_svc.sync_receipt(1)
        same = [t for t in receipt_after["tickets"] if t["ticket_no"] == ticket["ticket_no"]]
        assert len(same) == 1
        assert same[0]["import_count"] == 2
