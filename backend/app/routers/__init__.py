"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource, cross_module, sync_field_change

# sync_field_change 使用连字符前缀（/api/metadata-sync、/api/data-catalog），
# 与资源路由的下划线模块 key 不冲突，须先注册以免两段路径被 /{module}/{entry_id} 抢占。
ROUTERS = [sync_field_change, resource, cross_module]