"""业务路由汇总：通用资源路由 + 归档复算专用路由。"""
from __future__ import annotations

from app.routers import archive, resource

ROUTERS = [archive, resource]
