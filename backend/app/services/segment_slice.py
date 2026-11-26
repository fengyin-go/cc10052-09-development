"""切片分析器领域服务：管理实验差异的维度切片、切片值计算与失败重试。

数据链路位置：下钻分析层，消费显著性检验与归因分析的差异结果，按维度切片
计算分组差异，输出给报告生成器形成下钻章节。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SegmentSliceService(BaseModuleService):
    MODULE = "segment_slice"
    ENTITY = "切片结果"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["切片批次", "实验编码", "切片维度", "切片值", "分组差异"]
    REQUIRED_FIELDS = ["切片批次", "实验编码", "切片维度"]
    STATUS_ORDER = ["待计算", "已计算", "计算失败"]
    ACTION_RULES = {"开始计算": "已计算", "重试计算": "计算失败"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：维度切片与差异计算 ----

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """切片动作分派：开始计算走领域 run_slice，归档恢复维度经证据网关复算。"""
        if action == "开始计算":
            result = self.run_slice(entry_id)
            if not result.get("ok"):
                return None, str(result.get("message", "切片计算失败"))
            return store.find(self.MODULE, entry_id), str(result.get("message", "切片已计算"))
        if action == "重试计算":
            result = self.retry_failed(entry_id)
            if not result.get("ok"):
                return None, str(result.get("message", "仅计算失败切片可重试"))
            return store.find(self.MODULE, entry_id), str(result.get("message", "切片已回到待计算"))
        return super().run_action(entry_id, action)

    def run_slice(self, entry_id: int) -> dict[str, Any]:
        """执行切片计算：按维度聚合分组差异并写回结果。

        归档恢复维度必须按归档号关联已校验的归档文件，取回冻结检验数据复算，
        保证恢复后得到与归档同样区间、同样本量的切片结果；证据失效或复算包
        不完整时拒绝计算，避免半包进入恢复计算。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") not in ("待计算",):
            return {"ok": False, "message": "仅待计算切片可执行"}
        dimension = str(entry.get("切片维度", ""))
        if dimension == "归档恢复":
            return self._run_archive_recovery_slice(entry)
        exp_code = str(entry.get("实验编码", ""))
        assign_rows = [
            r for r in store.rows("variant_assign")
            if r.get("实验编码") == exp_code and r.get("status") != "已剔除"
        ]
        if not assign_rows:
            entry["status"] = "计算失败"
            entry["abnormal"] = True
            entry["失败原因"] = "无分组数据可切片"
            return {"ok": False, "message": "实验无分组数据，切片计算失败"}
        if dimension not in ("分组", "人群"):
            entry["status"] = "计算失败"
            entry["abnormal"] = True
            entry["失败原因"] = f"不支持的切片维度 {dimension}"
            return {"ok": False, "message": f"切片维度 {dimension} 不受支持"}
        ctrl = sum(1 for r in assign_rows if r.get("分组名称") == "对照组")
        treat = sum(1 for r in assign_rows if r.get("分组名称") == "实验组")
        diff = round((treat - ctrl) / max(len(assign_rows), 1), 4)
        entry["分组差异"] = str(diff)
        entry["status"] = "已计算"
        entry["pending"] = False
        entry["计算时间"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": f"切片 {entry.get('切片值', dimension)} 差异 {diff} 已计算"}

    def _run_archive_recovery_slice(self, entry: dict[str, Any]) -> dict[str, Any]:
        """归档恢复切片：以归档号取冻结证据复算，而不是读取在线分组明细。"""
        from app.services.archive_recovery import archive_recovery_service

        archive_no = str(entry.get("归档号", ""))
        if not archive_no:
            entry["status"] = "计算失败"
            entry["abnormal"] = True
            entry["失败原因"] = "切片未引用归档号，无法关联归档证据"
            return {"ok": False, "message": "归档恢复切片缺少归档号，拒绝计算"}
        check = archive_recovery_service.check_recovery_evidence(archive_no)
        if not check.get("ok"):
            entry["status"] = "计算失败"
            entry["abnormal"] = True
            entry["失败原因"] = str(check.get("message", "归档证据不可用"))
            return {"ok": False, "message": f"归档证据核查失败：{check.get('message', '')}"}
        evidence = archive_recovery_service.find_evidence(int(check["证据id"]))
        results = archive_recovery_service._recompute_from_evidence(evidence)
        entry["分组差异"] = str(results[-1]["组间差异"]) if results else ""
        entry["数据窗口"] = evidence.get("数据窗口", "")
        entry["样本量"] = str(check.get("样本量", {}))
        entry["引用证据id"] = evidence.get("id")
        entry["status"] = "已计算"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["计算时间"] = datetime.now(timezone.utc).isoformat()
        return {
            "ok": True,
            "message": (
                f"归档恢复切片按 {archive_no} 冻结证据复算完成，"
                f"区间 {evidence.get('数据窗口', '')}，样本量 {check.get('样本量', {})}"
            ),
        }

    def retry_failed(self, entry_id: int) -> dict[str, Any]:
        """重试失败切片：修复失败原因后回到待计算。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "计算失败":
            return {"ok": False, "message": "仅计算失败切片可重试"}
        reason = str(entry.get("失败原因", ""))
        if "维度" in reason:
            entry["切片维度"] = "分组"
        entry["status"] = "待计算"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["重试次数"] = str(int(entry.get("重试次数", "0") or 0) + 1)
        return {"ok": True, "message": f"切片已修复并回到待计算（第 {entry['重试次数']} 次）"}

    def batch_run_pending(self) -> dict[str, Any]:
        """批量执行全部待计算切片。"""
        pending = [r for r in store.rows(self.MODULE) if r.get("status") == "待计算"]
        done = sum(1 for r in pending if self.run_slice(int(r.get("id", 0))).get("ok"))
        return {"ok": True, "message": f"批量切片完成 {done}/{len(pending)}"}

    def consume_from_significance_test(self, exp_code: str) -> list[dict[str, Any]]:
        """读取显著性检验的置信区间，作为切片差异对照基线。"""
        return [
            {"指标编码": r.get("指标编码", ""), "置信区间": r.get("置信区间", "")}
            for r in store.rows("significance_test")
            if r.get("实验编码") == exp_code and r.get("status") == "已检验"
        ]

    def consume_from_cohort_builder(self, cohort_code: str) -> dict[str, Any]:
        """读取人群圈选器的人群包信息，限定切片范围。"""
        cohort = next((r for r in store.rows("cohort_builder") if r.get("人群编码") == cohort_code), None)
        if cohort is None:
            return {"ok": False, "message": f"人群包 {cohort_code} 不存在"}
        return {
            "ok": cohort.get("status") == "已生成",
            "人群规模": cohort.get("人群规模", ""),
            "状态": cohort.get("status", ""),
        }

    def produce_to_report_gen(self, exp_code: str) -> dict[str, Any]:
        """把已计算切片推送给报告生成器，登记下钻章节报告。"""
        computed = [
            r for r in store.rows(self.MODULE)
            if r.get("实验编码") == exp_code and r.get("status") == "已计算"
        ]
        if not computed:
            return {"ok": False, "message": "实验无已计算切片"}
        report_rows = store.rows("report_gen")
        report_rows.append({
            "id": max((int(r.get("id", 0)) for r in report_rows), default=0) + 1,
            "报告编码": f"RPT-SG-{exp_code}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": exp_code,
            "报告模板": "切片下钻模板",
            "数据窗口": "最近7天",
            "生成状态": "排队中",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"下钻报告已排队（覆盖 {len(computed)} 条切片）"}

    def export_slice_matrix(self, exp_code: str) -> list[dict[str, Any]]:
        """导出实验的切片矩阵，供指标查询服务提供下钻数据。"""
        return [
            {
                "切片维度": r.get("切片维度", ""),
                "切片值": r.get("切片值", ""),
                "分组差异": r.get("分组差异", ""),
            }
            for r in store.rows(self.MODULE)
            if r.get("实验编码") == exp_code and r.get("status") == "已计算"
        ]
