"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
支持跨模块查询、快照记录和流水日志，用于支撑跨模块数据流。
另持有两类跨模块共享状态：同步字段变更单（元数据同步 → 数据目录）和
数据目录字段生效状态（读湖表结构时以目录生效类型为准）。
"""
from __future__ import annotations

import copy
import datetime
from typing import Any

from app.seed import CATALOG_FIELD_SEED, SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 跨模块事件日志：记录所有跨模块操作
        self._events: list[dict[str, Any]] = []
        # 模块快照备份：用于检查点和恢复
        self._snapshots: dict[str, list[dict[str, Any]]] = {}
        # 同步字段变更单：元数据同步批次落库的差量文件，等待数据目录领取应用
        self._sync_tickets: list[dict[str, Any]] = []
        # 数据目录字段生效状态：表 -> 字段 -> {type, version, ticket_no, ...}
        self._catalog_fields: dict[str, dict[str, dict[str, Any]]] = {}
        # 目录表级版本：每次目录结构变更（独立修改或应用变更单）递增
        self._catalog_versions: dict[str, int] = {}
        for table, columns in CATALOG_FIELD_SEED.items():
            self._catalog_versions[table] = 1
            self._catalog_fields[table] = {
                field: {
                    "type": col_type,
                    "version": 1,
                    "ticket_no": "SEED",
                    "pending_review": False,
                    "review_from_ticket": "",
                    "updated_at": datetime.datetime.now().isoformat(),
                }
                for field, col_type in columns.items()
            }

    # ---- 基础读写 ----

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def find_by_field(self, module: str, field: str, value: str) -> list[dict[str, Any]]:
        """按指定字段值查找记录，支持跨模块关联查询。"""
        return [
            row for row in self.rows(module)
            if str(row.get(field, "")).casefold() == value.casefold()
        ]

    def upsert(self, module: str, entry: dict[str, Any]) -> dict[str, Any]:
        """插入或更新记录；存在相同 id 时覆盖，否则追加。"""
        rows = self.rows(module)
        entry_id = entry.get("id")
        for i, row in enumerate(rows):
            if row.get("id") == entry_id:
                rows[i] = entry
                return entry
        rows.append(entry)
        return entry

    # ---- 跨模块事件 ----

    def log_event(
        self, event_type: str, source_module: str, target_module: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """记录跨模块操作事件，供血缘和审计追溯。"""
        event = {
            "id": len(self._events) + 1,
            "event_type": event_type,
            "source_module": source_module,
            "target_module": target_module,
            "timestamp": datetime.datetime.now().isoformat(),
            "payload": payload,
        }
        self._events.append(event)
        return event

    def events_for_module(self, module: str) -> list[dict[str, Any]]:
        """查询与指定模块相关的所有事件。"""
        return [
            e for e in self._events
            if e["source_module"] == module or e["target_module"] == module
        ]

    def events_between(self, source: str, target: str) -> list[dict[str, Any]]:
        """查询两个模块之间的单向事件。"""
        return [
            e for e in self._events
            if e["source_module"] == source and e["target_module"] == target
        ]

    # ---- 快照与检查点 ----

    def create_snapshot(self, module: str) -> str:
        """为指定模块创建深拷贝快照，返回快照标识。"""
        timestamp = datetime.datetime.now().isoformat()
        key = f"{module}@{timestamp}"
        self._snapshots[key] = copy.deepcopy(self.rows(module))
        return key

    def get_snapshot(self, key: str) -> list[dict[str, Any]] | None:
        """按标识获取快照副本。"""
        raw = self._snapshots.get(key)
        return copy.deepcopy(raw) if raw is not None else None

    def snapshot_keys(self) -> list[str]:
        return sorted(self._snapshots)

    # ---- 概览 ----

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}

    def cross_module_stats(self) -> dict[str, object]:
        """跨模块统计：事件数、快照数、关联模块对。"""
        pairs: set[tuple[str, str]] = set()
        for e in self._events:
            pairs.add((e["source_module"], e["target_module"]))
        return {
            "event_count": len(self._events),
            "snapshot_count": len(self._snapshots),
            "linked_pairs": len(pairs),
            "linked_modules": sorted(set(
                m for p in pairs for m in p
            )),
        }

    # ---- 同步字段变更单 ----

    def add_sync_ticket(self, ticket: dict[str, Any]) -> dict[str, Any]:
        """变更单落库；id 在调用方编好后写入。"""
        self._sync_tickets.append(ticket)
        return ticket

    def next_sync_ticket_id(self) -> int:
        return max((int(t.get("id", 0)) for t in self._sync_tickets), default=0) + 1

    def find_sync_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        for ticket in self._sync_tickets:
            if int(ticket.get("id", 0)) == ticket_id:
                return ticket
        return None

    def find_sync_ticket_by_fingerprint(
        self, job_id: int, fingerprint: str
    ) -> dict[str, Any] | None:
        """幂等查找：同一同步任务下同一差量文件指纹只应有一张变更单。"""
        for ticket in self._sync_tickets:
            if (
                int(ticket.get("job_id", 0)) == job_id
                and ticket.get("file_fingerprint") == fingerprint
            ):
                return ticket
        return None

    def sync_tickets_for_job(self, job_id: int) -> list[dict[str, Any]]:
        return [t for t in self._sync_tickets if int(t.get("job_id", 0)) == job_id]

    # ---- 数据目录字段生效状态 ----

    def catalog_table_version(self, table: str) -> int:
        """目录表级版本：每次目录结构变更递增，供变更单核对目标端版本。"""
        return self._catalog_versions.get(table, 0)

    def catalog_fields(self, table: str) -> dict[str, dict[str, Any]]:
        return self._catalog_fields.setdefault(table, {})

    def catalog_tables(self) -> list[str]:
        return sorted(self._catalog_fields)

    def bump_catalog_field(
        self,
        table: str,
        field: str,
        col_type: str,
        ticket_no: str,
        *,
        pending_review: bool = False,
        review_from_ticket: str = "",
    ) -> dict[str, Any]:
        """写入/更新目录列：表版本 +1，列版本记为新的表版本，并记录来源变更单。"""
        version = self._catalog_versions.get(table, 0) + 1
        self._catalog_versions[table] = version
        column = {
            "type": col_type,
            "version": version,
            "ticket_no": ticket_no,
            "pending_review": pending_review,
            "review_from_ticket": review_from_ticket,
            "updated_at": datetime.datetime.now().isoformat(),
        }
        self.catalog_fields(table)[field] = column
        return column

    def drop_catalog_field(self, table: str, field: str) -> bool:
        fields = self.catalog_fields(table)
        if field not in fields:
            return False
        del fields[field]
        self._catalog_versions[table] = self._catalog_versions.get(table, 0) + 1
        return True

    def mark_catalog_field_review(
        self, table: str, field: str, flag: bool, review_from_ticket: str = ""
    ) -> None:
        """标记/清除列的待核对状态，不改变列的生效类型与版本。"""
        column = self.catalog_fields(table).get(field)
        if column is None:
            return
        column["pending_review"] = flag
        column["review_from_ticket"] = review_from_ticket if flag else ""


store = Store()
