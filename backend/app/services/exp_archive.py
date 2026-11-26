"""实验归档库领域服务：管理已结束实验的结论归档、保留等级与恢复流转。

数据链路位置：归档存储层，是实验评估数据沉淀的终点，消费实验设计与报告生成
的结论数据，按保留等级归档，支持恢复供切片分析复用历史明细。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.archive_recovery import archive_recovery_service
from app.services.base import BaseModuleService
from app.store import store


class ExpArchiveService(BaseModuleService):
    MODULE = "exp_archive"
    ENTITY = "归档实验"
    OPERATION_TERM = "归档"
    LIST_FIELDS = ["实验编码", "结束时间", "结论摘要", "归档位置", "保留等级"]
    REQUIRED_FIELDS = ["实验编码", "结束时间", "结论摘要"]
    STATUS_ORDER = ["待归档", "已归档", "恢复中"]
    ACTION_RULES = {"执行归档": "已归档", "恢复实验": "恢复中"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：结论归档与恢复复用 ----

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """归档库动作分派：恢复实验必须走证据网关，不能只做通用状态流转。"""
        if action == "恢复实验":
            result = self.restore(entry_id)
            if not result.get("ok"):
                return None, str(result.get("message", "恢复被拒绝"))
            return store.find(self.MODULE, entry_id), str(result.get("message", "实验恢复中"))
        if action == "执行归档":
            result = self.archive(entry_id)
            if not result.get("ok"):
                return None, str(result.get("message", "归档被拒绝"))
            return store.find(self.MODULE, entry_id), str(result.get("message", "实验已归档"))
        return super().run_action(entry_id, action)

    def archive(self, entry_id: int) -> dict[str, Any]:
        """执行归档：锁定结论摘要并标注归档位置与保留等级。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待归档":
            return {"ok": False, "message": "仅待归档实验可执行归档"}
        exp_code = str(entry.get("实验编码", ""))
        ended = any(
            r.get("实验编码") == exp_code and r.get("status") == "已结束"
            for r in store.rows("exp_design")
        )
        if not ended:
            return {"ok": False, "message": "实验尚未结束，禁止归档"}
        entry["status"] = "已归档"
        entry["pending"] = False
        entry["归档位置"] = f"archive/{exp_code}/{datetime.now(timezone.utc).strftime('%Y%m')}"
        entry["归档时间"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": f"实验 {exp_code} 已归档到 {entry['归档位置']}"}

    def restore(self, entry_id: int) -> dict[str, Any]:
        """恢复已归档实验：读取所引归档号并核查证据，通过后交由归档复算服务办理。

        半包（缺分组或完整性校验失败）与失效证据一律拒绝；同一归档号存在在途恢复时
        幂等拦截，不产生第二份有效恢复。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") not in ("已归档", "恢复中"):
            return {"ok": False, "message": "仅已归档实验可恢复"}
        archive_no = str(entry.get("归档号", ""))
        if not archive_no:
            return {"ok": False, "message": "该归档未登记归档号，无检验证据可复算"}
        result = archive_recovery_service.start_recovery(archive_no, operator="归档库恢复")
        if not result.get("ok"):
            return {"ok": False, "message": str(result.get("message", "恢复被拒绝"))}
        recovery = result.get("recovery", {})
        entry["status"] = "恢复中"
        entry["pending"] = True
        entry["恢复时间"] = datetime.now(timezone.utc).isoformat()
        entry["恢复单号"] = recovery.get("恢复单号", "")
        return {
            "ok": True,
            "message": (
                f"实验 {entry.get('实验编码', '')} 已按归档号 {archive_no} 引用"
                f"有效证据进入恢复（{recovery.get('恢复单号', '')}）"
            ),
        }

    def batch_archive_pending(self) -> dict[str, Any]:
        """批量归档全部待归档实验。"""
        pending = [r for r in store.rows(self.MODULE) if r.get("status") == "待归档"]
        done, skipped = 0, 0
        for row in pending:
            if self.archive(int(row.get("id", 0))).get("ok"):
                done += 1
            else:
                skipped += 1
        return {"ok": True, "message": f"批量归档完成 {done} 条，跳过 {skipped} 条"}

    def consume_from_report_gen(self, exp_code: str) -> list[dict[str, Any]]:
        """读取报告生成器中该实验的完成报告，作为归档结论来源。"""
        return [
            {"报告编码": r.get("报告编码", ""), "结论条数": r.get("结论条数", "0"), "数据窗口": r.get("数据窗口", "")}
            for r in store.rows("report_gen")
            if r.get("实验编码") == exp_code and r.get("status") == "已完成"
        ]

    def set_retention_level(self, entry_id: int, level: str) -> dict[str, Any]:
        """设置归档保留等级并联动清理策略。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or level not in ("永久", "标准", "临时"):
            return {"ok": False, "message": "归档记录不存在或保留等级非法"}
        entry["保留等级"] = level
        entry["pending"] = level != "标准"
        entry["保留更新时间"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": f"保留等级已设置为 {level}"}

    def purge_expired_temp(self) -> dict[str, Any]:
        """清理超过保留期的临时等级归档记录。

        清理前必须检查源证据占用：被校验通过的下载或在途恢复引用的临时归档冲突跳过，
        并由归档复算服务写审计，保证“后台何时取得源数据”可回查。
        """
        outcome = archive_recovery_service.purge_temp_archives()
        return {"ok": True, "message": str(outcome.get("message", "临时归档清理完成")),
                **{k: v for k, v in outcome.items() if k != "ok"}}

    def export_archive_index(self) -> list[dict[str, Any]]:
        """导出归档索引，供指标查询服务提供历史数据说明。"""
        return [
            {
                "实验编码": r.get("实验编码", ""),
                "结束时间": r.get("结束时间", ""),
                "归档位置": r.get("归档位置", ""),
                "保留等级": r.get("保留等级", ""),
            }
            for r in store.rows(self.MODULE) if r.get("status") in ("已归档", "恢复中")
        ]
