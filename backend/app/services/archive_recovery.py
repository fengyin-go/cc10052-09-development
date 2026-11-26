"""归档复算领域服务：归档检验证据、复算包打包导出与完整性校验、切片恢复与清理冲突。

数据链路位置：归档存储层与下钻分析层之间的证据网关。实验归档库按归档号+归档版次
冻结检验与分组证据；复算包下载只允许携带同一归档版次的检验与分组结果，并随包交付
逐文件哈希与检验清单作为检验证据；切片分析器办理恢复时只能引用同一归档号下仍可
使用的证据复算，半包（缺分组或哈希校验失败）一律不得进入恢复计算。

关键约束：
- 一次下载只含同一归档号/版次的数据，禁止拼接其他实验窗口。
- 下载办理结果可回查，缺件与校验失败文件定位到具体文件节（含所属分组）。
- 恢复按归档号关联已校验文件，读取冻结检验数据，复算得到同区间、同样本量结果。
- 读取中断后以同一归档号再次发起恢复不产生第二份有效恢复（在途恢复幂等）。
- 临时归档清理若与下载/在途恢复共用同一份源证据，冲突跳过并写审计，可回查。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.store import store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArchiveRecoveryService:
    """归档证据与复算恢复的领域逻辑（不挂通用资源路由，由 archive 路由专用）。"""

    EVIDENCE_TABLE = "archive_evidence"
    DOWNLOAD_TABLE = "archive_download"
    RECOVERY_TABLE = "archive_recovery"
    AUDIT_TABLE = "archive_audit_log"

    # ---- 证据内容与哈希：证据按归档号+版次冻结，文件节内容可确定性重建 ----

    @staticmethod
    def _file_payload(evidence: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
        """构造某个文件节在归档冻结时的逻辑内容（与版次绑定，不含其他窗口数据）。"""
        return {
            "归档号": evidence.get("归档号", ""),
            "归档版次": evidence.get("归档版次", ""),
            "数据窗口": evidence.get("数据窗口", ""),
            "检验时间": evidence.get("检验时间", ""),
            "文件": section.get("文件", ""),
            "分组": section.get("分组", ""),
            "分组结果": evidence.get("分组结果", []),
            "检验结果": evidence.get("检验结果", []),
        }

    @staticmethod
    def _canonical_bytes(payload: dict[str, Any]) -> bytes:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return text.encode("utf-8")

    def expected_hash(self, evidence: dict[str, Any], section: dict[str, Any]) -> tuple[str, int]:
        """证据冻结时登记的期望哈希与字节数（完整文件基准）。"""
        payload = self._file_payload(evidence, section)
        raw = self._canonical_bytes(payload)
        return hashlib.sha256(raw).hexdigest(), len(raw)

    def actual_hash(self, evidence: dict[str, Any], section: dict[str, Any]) -> tuple[str, int]:
        """当前物理副本上该文件节的实际哈希与字节数。

        - truncated：文件被截断，只保留前缀，字节数与哈希均与期望不符；
        - 缺件：该文件节不在副本清单内，由调用方按缺件处理；
        - 正常：与冻结基准完全一致。
        """
        expect_hash, expect_size = self.expected_hash(evidence, section)
        problem = str(section.get("问题", ""))
        if problem == "truncated":
            payload = self._file_payload(evidence, section)
            raw = self._canonical_bytes(payload)
            cut = max(1, len(raw) // 2)
            damaged = raw[:cut]
            return hashlib.sha256(damaged).hexdigest(), len(damaged)
        return expect_hash, expect_size

    @staticmethod
    def evidence_hash(evidence: dict[str, Any]) -> str:
        """整份证据（冻结检验数据）的指纹，随包交付并在恢复时留痕。"""
        payload = {
            "归档号": evidence.get("归档号", ""),
            "归档版次": evidence.get("归档版次", ""),
            "检验时间": evidence.get("检验时间", ""),
            "数据窗口": evidence.get("数据窗口", ""),
            "分组结果": evidence.get("分组结果", []),
            "检验结果": evidence.get("检验结果", []),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    # ---- 查询辅助 ----

    def _evidence_rows(self, archive_no: str) -> list[dict[str, Any]]:
        return [r for r in store.rows(self.EVIDENCE_TABLE) if r.get("归档号") == archive_no]

    def find_evidence(self, evidence_id: int) -> dict[str, Any] | None:
        return store.find(self.EVIDENCE_TABLE, evidence_id)

    def find_download(self, download_no: str) -> dict[str, Any] | None:
        return next(
            (r for r in store.rows(self.DOWNLOAD_TABLE) if r.get("下载单号") == download_no), None)

    def find_recovery(self, recovery_no: str) -> dict[str, Any] | None:
        return next(
            (r for r in store.rows(self.RECOVERY_TABLE) if r.get("恢复单号") == recovery_no), None)

    def archive_revision(self, archive_no: str) -> str | None:
        """归档号对应的归档版次；同一归档号只允许一个有效版次。"""
        rows = self._evidence_rows(archive_no)
        revisions = {str(r.get("归档版次", "")) for r in rows if r.get("归档版次")}
        return next(iter(revisions)) if len(revisions) == 1 else None

    def list_evidence(self, archive_no: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(self.EVIDENCE_TABLE)
        if archive_no:
            rows = [r for r in rows if r.get("归档号") == archive_no]
        result = []
        for row in rows:
            result.append({
                "证据id": row.get("id"),
                "证据编码": row.get("证据编码", ""),
                "归档号": row.get("归档号", ""),
                "归档版次": row.get("归档版次", ""),
                "实验编码": row.get("实验编码", ""),
                "副本类型": row.get("副本类型", ""),
                "状态": row.get("状态", ""),
                "检验时间": row.get("检验时间", ""),
                "数据窗口": row.get("数据窗口", ""),
                "sha256": self.evidence_hash(row),
                "文件节": list(row.get("文件节", [])),
                "分组结果": list(row.get("分组结果", [])),
                "检验结果": list(row.get("检验结果", [])),
            })
        return result

    # ---- 审计 ----

    def _audit(self, action: str, *, archive_no: str, revision: str, result: str, detail: str,
               operator: str, evidence_id: int | None = None,
               download_no: str = "", recovery_no: str = "") -> dict[str, Any]:
        rows = store.rows(self.AUDIT_TABLE)
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "时间": _now(),
            "动作": action,
            "归档号": archive_no,
            "归档版次": revision,
            "证据id": evidence_id,
            "下载单号": download_no,
            "恢复单号": recovery_no,
            "操作人": operator,
            "结果": result,
            "明细": detail,
        }
        rows.append(entry)
        return entry

    def list_audit(self, archive_no: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(self.AUDIT_TABLE)
        if archive_no:
            rows = [r for r in rows if r.get("归档号") == archive_no]
        return [dict(r) for r in rows]

    # ---- 复算包下载与完整性校验 ----

    def _verify_package(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """以证据冻结基准逐文件节校验，定位缺件与校验失败文件。"""
        expected_sections = self._expected_sections(evidence)
        present = {str(s.get("文件", "")): s for s in evidence.get("文件节", [])}
        packed: list[dict[str, Any]] = []
        missing: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        for section in expected_sections:
            file_name = str(section.get("文件", ""))
            group = str(section.get("分组", ""))
            expect_hash, expect_size = self.expected_hash(evidence, section)
            current = present.get(file_name)
            if current is None:
                missing.append({"文件": file_name, "分组": group, "问题": "文件缺失"})
                packed.append({
                    "文件": file_name, "分组": group,
                    "期望sha256": expect_hash, "实际sha256": "",
                    "期望字节": expect_size, "实际字节": 0,
                    "问题": "文件缺失", "校验通过": False,
                })
                continue
            actual_hash, actual_size = self.actual_hash(evidence, current)
            ok = (actual_hash == expect_hash and actual_size == expect_size)
            problem = "" if ok else "哈希/字节校验失败（文件可能被截断或篡改）"
            packed.append({
                "文件": file_name, "分组": group,
                "期望sha256": expect_hash, "实际sha256": actual_hash,
                "期望字节": expect_size, "实际字节": actual_size,
                "问题": problem, "校验通过": ok,
            })
            if not ok:
                failed.append({"文件": file_name, "分组": group, "问题": problem})
        return {
            "打包文件": packed,
            "缺件": missing,
            "校验失败文件": failed,
            "通过": not missing and not failed,
        }

    @staticmethod
    def _expected_sections(evidence: dict[str, Any]) -> list[dict[str, str]]:
        """完整复算包应包含的文件节：检验清单 + 每个分组 + 检验结果，缺一不可。"""
        sections = [{"文件": "manifest/检验清单.json", "分组": "manifest"}]
        for group in evidence.get("分组结果", []):
            name = str(group.get("分组名称", ""))
            sections.append({"文件": f"groups/{name}.parquet", "分组": name})
        for test in evidence.get("检验结果", []):
            metric = str(test.get("指标编码", ""))
            sections.append({"文件": f"tests/{metric}.parquet", "分组": "检验"})
        return sections

    def create_download(
        self, archive_no: str, revision: str, *, operator: str, evidence_id: int | None = None,
    ) -> dict[str, Any]:
        """从归档详情打包导出复算包，随包交付检验证据并立即完成完整性校验。"""
        evidence = self._select_evidence(archive_no, revision, evidence_id)
        if isinstance(evidence, str):
            return {"ok": False, "message": evidence}
        verify = self._verify_package(evidence)
        rows = store.rows(self.DOWNLOAD_TABLE)
        download_no = (
            f"DL-{archive_no.replace('AR-', '')}-"
            f"{max((int(r.get('id', 0)) for r in rows), default=0) + 1:04d}"
        )
        record = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "下载单号": download_no,
            "归档号": archive_no,
            "归档版次": revision,
            "实验编码": evidence.get("实验编码", ""),
            "证据id": evidence.get("id"),
            "操作人": operator or "未署名分析员",
            "发起时间": _now(),
            "办理状态": "校验通过" if verify["通过"] else "校验失败",
            "打包文件": verify["打包文件"],
            "缺件": verify["缺件"],
            "校验失败文件": verify["校验失败文件"],
            "证据清单": [{
                "证据编码": evidence.get("证据编码", ""),
                "副本类型": evidence.get("副本类型", ""),
                "检验时间": evidence.get("检验时间", ""),
                "sha256": self.evidence_hash(evidence),
            }],
        }
        rows.append(record)
        self._audit(
            "打包导出复算包",
            archive_no=archive_no, revision=revision,
            result="成功" if verify["通过"] else "完整性校验失败",
            detail=(
                f"证据 {evidence.get('证据编码', '')}（{evidence.get('副本类型', '')}），"
                f"{'全部文件节校验通过' if verify['通过'] else '半包不得交付恢复：' + self._describe_issues(verify)}"
            ),
            operator=operator or "未署名分析员",
            evidence_id=int(evidence.get("id", 0)), download_no=download_no,
        )
        return {
            "ok": verify["通过"],
            "message": (
                f"复算包 {download_no} 校验通过，含 {len(verify['打包文件'])} 个文件节"
                if verify["通过"]
                else f"复算包 {download_no} 完整性校验失败：{self._describe_issues(verify)}"
            ),
            "download": record,
        }

    def _select_evidence(
        self, archive_no: str, revision: str, evidence_id: int | None,
    ) -> dict[str, Any] | str:
        candidates = [
            r for r in self._evidence_rows(archive_no)
            if str(r.get("归档版次", "")) == revision
        ]
        if not candidates:
            return f"归档号 {archive_no} 的版次 {revision} 无检验证据"
        if evidence_id is not None:
            picked = next((r for r in candidates if int(r.get("id", 0)) == evidence_id), None)
            if picked is None:
                return f"证据 {evidence_id} 不属于归档号 {archive_no} / 版次 {revision}，禁止跨版次拼接"
            return picked
        usable = [r for r in candidates if r.get("状态") == "可使用"]
        if usable:
            return usable[0]
        return candidates[0]

    @staticmethod
    def _describe_issues(verify: dict[str, Any]) -> str:
        parts = []
        for item in verify["缺件"]:
            parts.append(f"缺件 {item['文件']}（分组：{item['分组']}）")
        for item in verify["校验失败文件"]:
            parts.append(f"校验失败 {item['文件']}（分组：{item['分组']}）")
        return "；".join(parts) or "未知完整性问题"

    def list_downloads(self, archive_no: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(self.DOWNLOAD_TABLE)
        if archive_no:
            rows = [r for r in rows if r.get("归档号") == archive_no]
        return [dict(r) for r in rows]

    def get_download_result(self, download_no: str) -> dict[str, Any]:
        """回查一次下载的办理结果，明确缺件与校验失败文件，阻止半包进入恢复。"""
        record = self.find_download(download_no)
        if record is None:
            return {"ok": False, "message": f"下载单 {download_no} 不存在"}
        return {"ok": True, "message": record.get("办理状态", ""), "download": dict(record)}

    def recheck_download(self, download_no: str) -> dict[str, Any]:
        """对已下载复算包重新执行完整性校验（模拟文件读取中断后的再次核查）。"""
        record = self.find_download(download_no)
        if record is None:
            return {"ok": False, "message": f"下载单 {download_no} 不存在"}
        evidence = self.find_evidence(int(record.get("证据id", 0)))
        if evidence is None or evidence.get("状态") != "可使用":
            record["办理状态"] = "证据失效"
            self._audit(
                "复算包复检",
                archive_no=str(record.get("归档号", "")),
                revision=str(record.get("归档版次", "")),
                result="证据失效",
                detail=f"下载 {download_no} 引用的证据已不可使用，复算包冻结",
                operator="系统复检", evidence_id=record.get("证据id"), download_no=download_no,
            )
            return {"ok": False, "message": "引用证据已失效，复算包不可用于恢复", "download": record}
        verify = self._verify_package(evidence)
        record["打包文件"] = verify["打包文件"]
        record["缺件"] = verify["缺件"]
        record["校验失败文件"] = verify["校验失败文件"]
        record["办理状态"] = "校验通过" if verify["通过"] else "校验失败"
        self._audit(
            "复算包复检",
            archive_no=str(record.get("归档号", "")),
            revision=str(record.get("归档版次", "")),
            result="通过" if verify["通过"] else "失败",
            detail=f"下载 {download_no} 复检：{self._describe_issues(verify) if not verify['通过'] else '全部文件节通过'}",
            operator="系统复检", evidence_id=int(evidence.get("id", 0)),
            download_no=download_no,
        )
        return {
            "ok": verify["通过"],
            "message": (
                "复算包复检通过" if verify["通过"]
                else f"复算包复检失败：{self._describe_issues(verify)}"
            ),
            "download": record,
        }

    # ---- 切片恢复 ----

    def bootstrap_seed_packages(self) -> None:
        """启动时把种子复算包的逐文件哈希/字节与证据指纹补齐（随包交付检验证据）。

        种子数据只描述文件节与问题类型，真实摘要由冻结证据确定性生成，避免手工
        维护哈希导致与证据内容漂移。
        """
        for download in store.rows(self.DOWNLOAD_TABLE):
            evidence = self.find_evidence(int(download.get("证据id", 0)))
            if evidence is None:
                continue
            for item in download.get("证据清单", []):
                if not item.get("sha256"):
                    item["sha256"] = self.evidence_hash(evidence)
            if download.get("办理状态") == "校验通过":
                verify = self._verify_package(evidence)
                if not download.get("打包文件") or any(
                        not f.get("期望sha256") for f in download.get("打包文件", [])):
                    download["打包文件"] = verify["打包文件"]
                    download["缺件"] = verify["缺件"]
                    download["校验失败文件"] = verify["校验失败文件"]
            elif not download.get("打包文件"):
                verify = self._verify_package(evidence)
                download["打包文件"] = verify["打包文件"]

    def active_recovery(self, archive_no: str) -> dict[str, Any] | None:
        return next(
            (r for r in store.rows(self.RECOVERY_TABLE)
             if r.get("归档号") == archive_no and r.get("办理状态") == "在途恢复"),
            None,
        )

    def valid_recovery(self, archive_no: str) -> dict[str, Any] | None:
        return next(
            (r for r in store.rows(self.RECOVERY_TABLE)
             if r.get("归档号") == archive_no and r.get("办理状态") in ("在途恢复", "已复算")),
            None,
        )

    def check_recovery_evidence(self, archive_no: str) -> dict[str, Any]:
        """打开切片恢复时读取所引归档号并核查对应证据是否仍可使用。"""
        revision = self.archive_revision(archive_no)
        if revision is None:
            return {"ok": False, "message": f"归档号 {archive_no} 不存在或版次不唯一"}
        usable = [
            r for r in self._evidence_rows(archive_no)
            if r.get("状态") == "可使用" and str(r.get("归档版次", "")) == revision
        ]
        good_download = next(
            (r for r in store.rows(self.DOWNLOAD_TABLE)
             if r.get("归档号") == archive_no and r.get("办理状态") == "校验通过"),
            None,
        )
        if not usable:
            return {"ok": False, "message": f"归档号 {archive_no} 无可用证据，恢复被拒绝",
                    "归档版次": revision}
        if good_download is None:
            return {"ok": False, "message": "缺少校验通过的复算包，半包不得进入恢复",
                    "归档版次": revision}
        evidence = usable[0]
        return {
            "ok": True,
            "message": "证据可使用，允许发起恢复",
            "归档号": archive_no,
            "归档版次": revision,
            "证据编码": evidence.get("证据编码", ""),
            "证据id": evidence.get("id"),
            "下载单号": good_download.get("下载单号", ""),
            "数据窗口": evidence.get("数据窗口", ""),
            "样本量": {g.get("分组名称", ""): g.get("样本量", 0)
                      for g in evidence.get("分组结果", [])},
        }

    def start_recovery(self, archive_no: str, *, operator: str,
                       download_no: str | None = None) -> dict[str, Any]:
        """以同一归档号的有效证据发起切片恢复；恢复幂等，不产生第二份有效恢复。

        同一归档号已存在在途或已复算的有效恢复时（含文件读取中断后再次发起），
        一律返回原恢复单，只有复算失败后才允许重新发起。
        """
        existing = self.valid_recovery(archive_no)
        if existing is not None:
            self._audit(
                "重复发起切片恢复",
                archive_no=archive_no, revision=str(existing.get("归档版次", "")),
                result="幂等拦截",
                detail=(f"有效恢复 {existing.get('恢复单号', '')}"
                        f"（{existing.get('办理状态', '')}）已存在，"
                        "沿用同一证据，不新建第二份有效恢复"),
                operator=operator or "未署名分析员",
                evidence_id=existing.get("证据id"),
                download_no=str(existing.get("下载单号", "")),
                recovery_no=str(existing.get("恢复单号", "")),
            )
            return {
                "ok": False,
                "message": (
                    f"归档号 {archive_no} 已存在有效恢复 {existing.get('恢复单号', '')}"
                    f"（{existing.get('办理状态', '')}），未产生新恢复"
                ),
                "recovery": existing,
                "幂等": True,
            }
        check = self.check_recovery_evidence(archive_no)
        if not check.get("ok"):
            return {"ok": False, "message": str(check.get("message", "证据核查未通过"))}
        revision = str(check["归档版次"])
        evidence = self.find_evidence(int(check["证据id"]))
        chosen_download_no = download_no or str(check["下载单号"])
        download = self.find_download(chosen_download_no)
        if download is None or download.get("办理状态") != "校验通过":
            return {"ok": False, "message": "所选复算包未通过完整性校验，不能恢复"}
        if str(download.get("归档号", "")) != archive_no or \
                str(download.get("归档版次", "")) != revision:
            return {"ok": False, "message": "复算包与归档号/版次不一致，禁止跨窗口拼接恢复"}
        rows = store.rows(self.RECOVERY_TABLE)
        seq = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        recovery_no = f"RC-{archive_no.replace('AR-', '')}-{seq:04d}"
        archive_entry = next(
            (r for r in store.rows("exp_archive") if r.get("归档号") == archive_no), None)
        slice_batch = f"SG-AR-{archive_entry.get('id', 0) if archive_entry else 0}"
        record = {
            "id": seq,
            "恢复单号": recovery_no,
            "归档号": archive_no,
            "归档版次": revision,
            "实验编码": str(evidence.get("实验编码", "")),
            "下载单号": chosen_download_no,
            "证据id": evidence.get("id"),
            "操作人": operator or "未署名分析员",
            "发起时间": _now(),
            "办结时间": "",
            "办理状态": "在途恢复",
            "切片批次": slice_batch,
            "数据窗口": evidence.get("数据窗口", ""),
            "样本量": {g.get("分组名称", ""): g.get("样本量", 0)
                      for g in evidence.get("分组结果", [])},
            "切片结果": [],
            "失败原因": "",
        }
        rows.append(record)
        if archive_entry is not None and archive_entry.get("status") != "恢复中":
            archive_entry["status"] = "恢复中"
            archive_entry["pending"] = True
        self._audit(
            "发起切片恢复",
            archive_no=archive_no, revision=revision, result="成功",
            detail=(f"经下载 {chosen_download_no} 引用证据 {evidence.get('证据编码', '')}，"
                    f"取回 {evidence.get('检验时间', '')} 冻结检验数据复算"),
            operator=operator or "未署名分析员",
            evidence_id=int(evidence.get("id", 0)),
            download_no=chosen_download_no, recovery_no=recovery_no,
        )
        return {"ok": True, "message": f"恢复 {recovery_no} 已发起，等待切片复算办结",
                "recovery": record}

    def complete_recovery(self, recovery_no: str) -> dict[str, Any]:
        """办结在途恢复：再次核查证据可用后，用冻结数据复算，得到同区间同样本量结果。"""
        record = self.find_recovery(recovery_no)
        if record is None:
            return {"ok": False, "message": f"恢复单 {recovery_no} 不存在"}
        if record.get("办理状态") != "在途恢复":
            return {"ok": False, "message": f"恢复 {recovery_no} 状态为 {record.get('办理状态')}，无需办结"}
        evidence = self.find_evidence(int(record.get("证据id", 0)))
        if evidence is None or evidence.get("状态") != "可使用":
            record["办理状态"] = "复算失败"
            record["失败原因"] = "引用证据已失效，拒绝使用不可信证据复算"
            self._audit(
                "办结切片恢复",
                archive_no=str(record.get("归档号", "")),
                revision=str(record.get("归档版次", "")),
                result="失败", detail=record["失败原因"],
                operator="切片分析器", evidence_id=record.get("证据id"),
                download_no=str(record.get("下载单号", "")), recovery_no=recovery_no,
            )
            return {"ok": False, "message": record["失败原因"], "recovery": record}
        download = self.find_download(str(record.get("下载单号", "")))
        if download is None or download.get("办理状态") != "校验通过":
            record["办理状态"] = "复算失败"
            record["失败原因"] = "复算包未通过完整性校验，不能作为恢复输入"
            return {"ok": False, "message": record["失败原因"], "recovery": record}
        slice_results = self._recompute_from_evidence(evidence)
        record["切片结果"] = slice_results
        record["办理状态"] = "已复算"
        record["办结时间"] = _now()
        archive_entry = next(
            (r for r in store.rows("exp_archive")
             if r.get("归档号") == record.get("归档号")), None)
        if archive_entry is not None:
            archive_entry["status"] = "已归档"
            archive_entry["pending"] = False
        self._materialize_slice(record, evidence, slice_results)
        self._audit(
            "办结切片恢复",
            archive_no=str(record.get("归档号", "")),
            revision=str(record.get("归档版次", "")),
            result="成功",
            detail=(f"使用证据 {evidence.get('证据编码', '')} 复算完成，"
                    f"区间 {record.get('数据窗口', '')}，"
                    f"样本量 {json.dumps(record.get('样本量', {}), ensure_ascii=False)}"),
            operator="切片分析器", evidence_id=int(evidence.get("id", 0)),
            download_no=str(record.get("下载单号", "")), recovery_no=recovery_no,
        )
        return {"ok": True, "message": f"恢复 {recovery_no} 已复算办结",
                "recovery": record, "切片结果": slice_results}

    @staticmethod
    def _recompute_from_evidence(evidence: dict[str, Any]) -> list[dict[str, Any]]:
        """基于冻结分组与检验数据复算，结果区间与样本量与归档时严格一致。"""
        groups = evidence.get("分组结果", [])
        means = {str(g.get("分组名称", "")): float(g.get("分组均值", 0)) for g in groups}
        sizes = {str(g.get("分组名称", "")): int(g.get("样本量", 0)) for g in groups}
        ctrl_mean = means.get("对照组", 0.0)
        results: list[dict[str, Any]] = []
        for test in evidence.get("检验结果", []):
            for group in groups:
                name = str(group.get("分组名称", ""))
                results.append({
                    "指标编码": test.get("指标编码", ""),
                    "切片维度": "归档恢复",
                    "分组": name,
                    "数据窗口": evidence.get("数据窗口", ""),
                    "样本量": sizes.get(name, 0),
                    "分组均值": group.get("分组均值", ""),
                    "组间差异": round(means.get(name, 0.0) - ctrl_mean, 4),
                    "P值": test.get("P值", ""),
                    "置信区间": test.get("置信区间", ""),
                })
        return results

    def _materialize_slice(
        self, recovery: dict[str, Any], evidence: dict[str, Any],
        slice_results: list[dict[str, Any]],
    ) -> None:
        """把复算结果落到切片分析器批次，批次按归档号关联冻结证据。"""
        slice_rows = store.rows("segment_slice")
        existing = next(
            (r for r in slice_rows if r.get("切片批次") == recovery.get("切片批次")), None)
        payload = {
            "实验编码": recovery.get("实验编码", ""),
            "切片维度": "归档恢复",
            "切片值": f"{recovery.get('归档号', '')}/{recovery.get('归档版次', '')}",
            "分组差异": json.dumps(
                {r["分组"]: r["组间差异"] for r in slice_results}, ensure_ascii=False),
            "归档号": recovery.get("归档号", ""),
            "归档版次": recovery.get("归档版次", ""),
            "引用证据id": recovery.get("证据id"),
            "引用下载单号": recovery.get("下载单号", ""),
            "数据窗口": recovery.get("数据窗口", ""),
            "样本量": json.dumps(recovery.get("样本量", {}), ensure_ascii=False),
            "status": "已计算",
            "pending": False,
            "abnormal": False,
            "计算时间": _now(),
        }
        if existing is not None:
            existing.update(payload)
            return
        slice_rows.append({
            "id": max((int(r.get("id", 0)) for r in slice_rows), default=0) + 1,
            "切片批次": recovery.get("切片批次", ""),
            **payload,
        })

    def list_recoveries(self, archive_no: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(self.RECOVERY_TABLE)
        if archive_no:
            rows = [r for r in rows if r.get("归档号") == archive_no]
        return [dict(r) for r in rows]

    # ---- 临时归档清理冲突 ----

    def purge_temp_archives(self) -> dict[str, Any]:
        """清理临时归档；若源证据被下载或在途恢复占用则冲突跳过，全过程写审计。"""
        purged, blocked = 0, 0
        details: list[dict[str, Any]] = []
        for row in store.rows("exp_archive"):
            if row.get("保留等级") != "临时" or row.get("status") not in ("已归档", "恢复中"):
                continue
            archive_no = str(row.get("归档号", ""))
            if not archive_no:
                continue
            busy_downloads = [
                str(r.get("下载单号", "")) for r in store.rows(self.DOWNLOAD_TABLE)
                if r.get("归档号") == archive_no and r.get("办理状态") == "校验通过"
            ]
            busy_recovery = self.active_recovery(archive_no)
            evidence_ids = [int(e.get("id", 0)) for e in self._evidence_rows(archive_no)]
            audit_evidence = evidence_ids[0] if evidence_ids else None
            if busy_downloads or busy_recovery is not None:
                blocked += 1
                reason = (
                    f"源证据被下载 {','.join(busy_downloads) or '无'} 与在途恢复 "
                    f"{busy_recovery.get('恢复单号', '') if busy_recovery else '无'} 占用，未取得源数据"
                )
                self._audit(
                    "临时归档清理",
                    archive_no=archive_no, revision=str(row.get("归档版次", "")),
                    result="冲突跳过", detail=reason, operator="后台清理",
                    evidence_id=audit_evidence,
                )
                details.append({"归档号": archive_no, "结果": "冲突跳过", "明细": reason})
                continue
            if row.get("status") == "恢复中":
                blocked += 1
                reason = "归档仍处于恢复中，恢复未办结，后台未取得源数据"
                self._audit(
                    "临时归档清理",
                    archive_no=archive_no, revision=str(row.get("归档版次", "")),
                    result="冲突跳过", detail=reason, operator="后台清理",
                    evidence_id=audit_evidence,
                )
                details.append({"归档号": archive_no, "结果": "冲突跳过", "明细": reason})
                continue
            row["status"] = "已清理"
            row["pending"] = False
            row["abnormal"] = True
            for evidence in self._evidence_rows(archive_no):
                evidence["状态"] = "已清理"
            purged += 1
            self._audit(
                "临时归档清理",
                archive_no=archive_no, revision=str(row.get("归档版次", "")),
                result="已清理", detail="后台清理取得源数据并清理临时归档与证据副本",
                operator="后台清理", evidence_id=audit_evidence,
            )
            details.append({"归档号": archive_no, "结果": "已清理", "明细": "源数据已取得并清理"})
        return {
            "ok": True,
            "message": f"临时归档清理完成：清理 {purged} 条，冲突跳过 {blocked} 条",
            "清理": purged,
            "冲突跳过": blocked,
            "明细": details,
        }


archive_recovery_service = ArchiveRecoveryService()
