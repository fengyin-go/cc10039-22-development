"""数据目录领域服务：维护湖表字段和业务含义的数据目录与元数据。

目录侧持有湖表字段的"生效类型"：元数据同步模块产生的同步字段变更单，
由数据目录在同步作业的回执内领取并按字段应用；后续读湖表结构时以目录
生效的类型为准，每列可追溯到来源变更单。目标列在差量文件下载后被独立
修改且与导入类型冲突时，保留目标端当前值并列入待核对范围。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 差量文件中合法的字段变更动作
DIFF_CHANGE_TYPES = {"add", "drop", "type"}

# 变更单差量条目的应用结果
RESULT_APPLIED = "已应用"
RESULT_SKIPPED = "已一致跳过"
RESULT_CONFLICT = "冲突保留目标"


class DataCatalogService(BaseModuleService):
    MODULE = "data_catalog"
    ENTITY = "目录条目"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["条目名称", "数据层级", "所属湖表", "字段定义", "安全等级"]
    REQUIRED_FIELDS = ["条目名称", "数据层级", "所属湖表"]
    STATUS_ORDER = ["待发布", "已发布", "已过期"]
    ACTION_RULES = {"发布条目": "已发布", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = []

    # ---- 读湖表结构：以目录生效类型为准 ----

    def effective_schema(self, table: str) -> dict[str, Any]:
        """输出湖表当前生效结构，每列带来源变更单号供追溯。"""
        fields = store.catalog_fields(table)
        return {
            "table": table,
            "version": store.catalog_table_version(table),
            "columns": [
                {
                    "field": field,
                    "type": column["type"],
                    "version": column["version"],
                    "ticket_no": column["ticket_no"],
                    "pending_review": column["pending_review"],
                    "review_from_ticket": column["review_from_ticket"],
                }
                for field, column in sorted(fields.items())
            ],
        }

    def update_column_type(
        self, table: str, field: str, new_type: str
    ) -> tuple[dict[str, Any] | None, str]:
        """值班员在目录侧独立调整某列类型；视为人工核对，清除待核对标记。"""
        if not new_type.strip():
            return None, "目标类型不能为空"
        fields = store.catalog_fields(table)
        if field not in fields:
            return None, f"目录中不存在字段 {table}.{field}"
        column = store.bump_catalog_field(table, field, new_type.strip(), "MANUAL")
        store.log_event("catalog_column_updated", "data_catalog", "data_catalog", {
            "table": table,
            "field": field,
            "type": new_type.strip(),
            "version": column["version"],
        })
        return column, f"目录字段 {table}.{field} 已独立调整为 {new_type.strip()}（目录版本 v{column['version']}）"

    def pending_review_scope(self, table: str | None = None) -> list[dict[str, Any]]:
        """待核对范围：目录中仍被标记为冲突待核对的列。"""
        scope: list[dict[str, Any]] = []
        for name in store.catalog_tables():
            if table and name != table:
                continue
            for field, column in sorted(store.catalog_fields(name).items()):
                if column.get("pending_review"):
                    scope.append({
                        "table": name,
                        "field": field,
                        "type": column["type"],
                        "version": column["version"],
                        "review_from_ticket": column["review_from_ticket"],
                    })
        return scope

    # ---- 应用同步字段变更单：按字段裁决确认过的差量 ----

    def apply_field_diffs(
        self,
        table: str,
        diffs: list[dict[str, Any]],
        base_version: int,
        ticket_no: str,
    ) -> dict[str, Any]:
        """按字段应用变更单中的差量，返回每字段裁决结果。

        裁决规则（base_version 为差量文件下载时的目标端版本）：
        - 列在文件下载后被独立修改（列版本 > base_version）且与导入类型冲突，
          保留目标端当前值，列入待核对范围；
        - 目标现状已与导入结果一致时跳过，不重复变更；
        - 其余情况应用导入差量，文件以外的字段不受此轮变更影响。
        """
        applied: list[str] = []
        skipped: list[str] = []
        pending: list[str] = []
        ruled_diffs: list[dict[str, Any]] = []

        for diff in diffs:
            field = str(diff.get("field", "")).strip()
            change = str(diff.get("change", "")).strip()
            to_type = str(diff.get("to", "")).strip()
            column = store.catalog_fields(table).get(field)
            concurrently_modified = (
                column is not None and int(column["version"]) > base_version
            )

            if change == "add":
                if column is None:
                    store.bump_catalog_field(table, field, to_type, ticket_no)
                    applied.append(field)
                    result, note = RESULT_APPLIED, "新增字段已写入目录"
                elif column["type"] == to_type:
                    skipped.append(field)
                    result, note = RESULT_SKIPPED, "目录已存在同类型字段"
                else:
                    store.mark_catalog_field_review(table, field, True, ticket_no)
                    pending.append(field)
                    result, note = RESULT_CONFLICT, "目录已存在该字段且类型不一致，保留目标端定义"
            elif change == "type":
                if column is None:
                    pending.append(field)
                    result, note = RESULT_CONFLICT, "目标端已不存在该字段，保留目标端现状"
                elif concurrently_modified and column["type"] != to_type:
                    store.mark_catalog_field_review(table, field, True, ticket_no)
                    pending.append(field)
                    result, note = RESULT_CONFLICT, "目标列在文件下载后被独立修改，保留目标端类型"
                elif column["type"] == to_type:
                    skipped.append(field)
                    result, note = RESULT_SKIPPED, "目录类型已与导入一致"
                else:
                    store.bump_catalog_field(table, field, to_type, ticket_no)
                    applied.append(field)
                    result, note = RESULT_APPLIED, "字段类型已按导入差量更新"
            elif change == "drop":
                if column is None:
                    skipped.append(field)
                    result, note = RESULT_SKIPPED, "目录中已不存在该字段"
                elif concurrently_modified:
                    store.mark_catalog_field_review(table, field, True, ticket_no)
                    pending.append(field)
                    result, note = RESULT_CONFLICT, "目标列在文件下载后被独立修改，暂不删除"
                else:
                    store.drop_catalog_field(table, field)
                    applied.append(field)
                    result, note = RESULT_APPLIED, "字段已从目录移除"
            else:
                skipped.append(field)
                result, note = RESULT_SKIPPED, f"未知变更动作 {change}，未处理"

            ruled_diffs.append({**diff, "result": result, "note": note})

        return {
            "diffs": ruled_diffs,
            "applied": applied,
            "skipped": skipped,
            "pending_review": pending,
        }
