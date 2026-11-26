"""归档复算全链路场景测试（标准库断言，不依赖 pytest）。

覆盖：
1. R8 下载：完整包通过、截断件与缺件件定位到文件节并拒绝半包；
2. 证据核查、在途恢复幂等（中断重发不产生第二份有效恢复）；
3. 办结恢复使用完整副本，复算得到同区间、同样本量结果；
4. 临时归档清理与下载/恢复占用冲突可回查；
5. 跨归档号/版次拼接被拒绝；
6. 归档库 restore 与切片分析器归档恢复切片走同一证据网关。
"""
from __future__ import annotations

import json
import os
import sys

# 允许以 `python -I tests/test_archive_recovery.py` 从 backend 目录直接运行（-I 忽略 PYTHONPATH）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.seed import SEED_ROWS
from app.store import Store
import app.services as service_pkg
from app.services import archive_recovery as ar_module
from app.services.archive_recovery import ArchiveRecoveryService
from app.services.exp_archive import ExpArchiveService
from app.services.segment_slice import SegmentSliceService

# 每个用例使用全新内存仓库与全新服务，避免单例串扰。
def fresh_setup() -> tuple[Store, ArchiveRecoveryService, ExpArchiveService, SegmentSliceService]:
    store = Store()
    svc = ArchiveRecoveryService()
    archive_svc = ExpArchiveService()
    slice_svc = SegmentSliceService()
    ar_module.store = store  # type: ignore[attr-defined]
    service_pkg.__dict__  # noqa: B018 - 确保模块已导入
    # 重新绑定各服务模块里 import 进来的 store 单例
    import app.services.exp_archive as ea_mod
    import app.services.segment_slice as ss_mod
    ea_mod.store = store  # type: ignore[attr-defined]
    ss_mod.store = store  # type: ignore[attr-defined]
    svc.bootstrap_seed_packages()
    return store, svc, archive_svc, slice_svc


PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def test_seed_tables() -> None:
    print("[1] 种子数据：R8 归档号、三类证据副本、下载与在途恢复")
    assert "archive_evidence" in SEED_ROWS and "archive_download" in SEED_ROWS
    assert "archive_recovery" in SEED_ROWS and "archive_audit_log" in SEED_ROWS
    store, svc, _, _ = fresh_setup()
    archive = next(r for r in store.rows("exp_archive") if r.get("归档号") == "AR-R8")
    check("R8 归档存在且为临时保留/恢复中",
          archive["保留等级"] == "临时" and archive["status"] == "恢复中")
    evidence = svc.list_evidence("AR-R8")
    check("R8 有 3 个证据副本", len(evidence) == 3)
    check("仅 1 个完整副本可使用",
          sum(1 for e in evidence if e["状态"] == "可使用") == 1)
    check("证据冻结时间保留 20:20 检验样本",
          all("20:20" in e["检验时间"] for e in evidence))
    sizes = {g["分组名称"]: g["样本量"] for g in evidence[0]["分组结果"]}
    check("完整副本含两个分组各 1200 样本", sizes == {"对照组": 1200, "实验组": 1200})


def test_download_verification() -> None:
    print("[2] 复算包下载：完整通过、截断/缺件定位文件节，半包不可恢复")
    store, svc, _, _ = fresh_setup()
    # 完整副本（证据 id=1）
    ok = svc.create_download("AR-R8", "R8-v1", operator="分析员-甲", evidence_id=1)
    check("完整副本下载校验通过", ok["ok"] is True, ok.get("message", ""))
    dl_ok = ok["download"]
    check("随包交付证据清单与 sha256",
          dl_ok["证据清单"][0]["sha256"] and len(dl_ok["证据清单"][0]["sha256"]) == 64)
    check("4 个文件节全部逐文件哈希通过",
          all(f["校验通过"] for f in dl_ok["打包文件"]) and len(dl_ok["打包文件"]) == 4)
    # 截断副本（证据 id=2）
    bad1 = svc.create_download("AR-R8", "R8-v1", operator="分析员-甲", evidence_id=2)
    check("截断副本下载判定失败", bad1["ok"] is False)
    failed = bad1["download"]["校验失败文件"]
    check("截断问题定位到实验组文件节",
          len(failed) == 1 and failed[0]["文件"] == "groups/实验组.parquet"
          and failed[0]["分组"] == "实验组", json.dumps(failed, ensure_ascii=False))
    sec = next(f for f in bad1["download"]["打包文件"]
               if f["文件"] == "groups/实验组.parquet")
    check("截断件实际字节小于期望字节且哈希不符",
          sec["实际字节"] < sec["期望字节"] and sec["实际sha256"] != sec["期望sha256"])
    # 缺件副本（证据 id=3）
    bad2 = svc.create_download("AR-R8", "R8-v1", operator="分析员-甲", evidence_id=3)
    check("缺件副本下载判定失败", bad2["ok"] is False)
    missing = bad2["download"]["缺件"]
    check("缺件定位到缺失分组文件节",
          len(missing) == 1 and missing[0]["分组"] == "实验组"
          and missing[0]["文件"] == "groups/实验组.parquet", json.dumps(missing, ensure_ascii=False))
    # 回查办理结果
    query = svc.get_download_result(bad2["download"]["下载单号"])
    check("归档详情可回查下载办理结果与缺件",
          query["ok"] and query["download"]["办理状态"] == "校验失败"
          and query["download"]["缺件"])
    check("校验失败的下载不能作为恢复输入",
          svc.start_recovery("AR-R8", operator="分析员-丙",
                             download_no=bad1["download"]["下载单号"])["ok"] is False)


def test_cross_archive_revision_rejected() -> None:
    print("[3] 同一下载只能含同一归档版次，禁止拼接其他实验窗口")
    store, svc, _, _ = fresh_setup()
    check("不存在的版次被拒绝",
          svc.create_download("AR-R8", "R8-v9", operator="x")["ok"] is False)
    check("不存在的归档号被拒绝",
          svc.check_recovery_evidence("AR-NOPE")["ok"] is False)
    # 伪造一个其他窗口的证据，确认其不会被 AR-R8 的下载选中
    store.rows("archive_evidence").append({
        "id": 99, "归档号": "AR-OTHER", "归档版次": "O-v1", "实验编码": "EXP-OTHER",
        "证据编码": "EV-OTHER", "副本类型": "完整副本", "状态": "可使用",
        "检验时间": "2026-09-01T08:00:00Z", "数据窗口": "2026-08-25~2026-09-01",
        "分组结果": [{"分组名称": "对照组", "样本量": 10, "分组均值": "0.1"}],
        "检验结果": [{"指标编码": "M1", "P值": "0.9", "置信区间": "[0,0]"}],
        "文件节": [{"文件": "manifest/检验清单.json", "分组": "manifest", "问题": ""},
                  {"文件": "groups/对照组.parquet", "分组": "对照组", "问题": ""},
                  {"文件": "tests/M1.parquet", "分组": "检验", "问题": ""}],
    })
    pick = svc.create_download("AR-R8", "R8-v1", operator="x")
    check("AR-R8 下载不会引用其他窗口证据",
          pick["download"]["证据id"] == 1 and pick["download"]["实验编码"] == "EXP-R8")


def test_recovery_idempotent_and_recompute() -> None:
    print("[4] 恢复幂等 + 办结复算同区间同样本量 + 切片落库")
    store, svc, _, slice_svc = fresh_setup()
    # 种子里已存在在途恢复 RC-R8-0001（分析员-乙发起）
    first = svc.start_recovery("AR-R8", operator="分析员-甲")
    check("在途恢复期间再次发起被幂等拦截",
          first["ok"] is False and first.get("幂等") is True)
    check("未产生第二份恢复单",
          len(store.rows("archive_recovery")) == 1)
    rc = store.rows("archive_recovery")[0]
    check("在途恢复引用完整副本证据 id=1", rc["证据id"] == 1)
    done = svc.complete_recovery(rc["恢复单号"])
    check("在途恢复办结成功", done["ok"] is True, done.get("message", ""))
    check("办结后状态为已复算", done["recovery"]["办理状态"] == "已复算")
    # 复算结果区间与样本量
    results = done["切片结果"]
    check("复算覆盖冻结数据窗口",
          all(r["数据窗口"] == "2026-10-11~2026-10-18" for r in results))
    check("复算样本量与归档冻结一致（1200/1200）",
          {r["分组"]: r["样本量"] for r in results if r["指标编码"] == "METR-CONV"}
          == {"对照组": 1200, "实验组": 1200})
    check("复算保留 20:20 检验 P 值与置信区间",
          all(r["P值"] == "0.0183" and r["置信区间"] == "[0.0051,0.0649]" for r in results))
    # 确定性：同一证据复算两次结果一致
    again = svc._recompute_from_evidence(svc.find_evidence(1))
    check("同一证据复算结果确定性一致",
          json.dumps(again, sort_keys=True, ensure_ascii=False)
          == json.dumps(results, sort_keys=True, ensure_ascii=False))
    # 办结后再次发起恢复仍不得产生第二份有效恢复
    retry = svc.start_recovery("AR-R8", operator="分析员-甲")
    check("办结后再发起仍被拦截，不产生另一份有效恢复",
          retry["ok"] is False and len(store.rows("archive_recovery")) == 1)
    # 切片批次落库并带归档号关联
    slices = [r for r in store.rows("segment_slice") if r.get("归档号") == "AR-R8"]
    check("复算结果落到切片分析器且关联归档号/证据",
          len(slices) == 1 and slices[0]["status"] == "已计算"
          and slices[0]["引用证据id"] == 1)
    check("切片样本量字段与冻结一致",
          json.loads(slices[0]["样本量"]) == {"对照组": 1200, "实验组": 1200})


def test_evidence_invalidated_blocks_recovery() -> None:
    print("[5] 证据失效（截断件不可信）时恢复与复检均被拒绝")
    store, svc, _, _ = fresh_setup()
    # 让完整副本失效，只剩截断/缺件副本
    store.rows("archive_evidence")[0]["状态"] = "已失效"
    check("无可使用证据时核查不通过",
          svc.check_recovery_evidence("AR-R8")["ok"] is False)
    check("发起恢复被拒绝",
          svc.start_recovery("AR-R8", operator="x")["ok"] is False)
    # 复检一个引用完整副本的下载：证据失效后冻结
    recheck = svc.recheck_download("DL-R8-0001")
    check("引用证据失效后复检失败并标记证据失效",
          recheck["ok"] is False and recheck["download"]["办理状态"] == "证据失效")


def test_purge_conflict() -> None:
    print("[6] 临时归档清理与下载/在途恢复共用源证据 -> 冲突跳过且可回查")
    store, svc, _, _ = fresh_setup()
    outcome = svc.purge_temp_archives()
    check("清理因占用冲突跳过 R8",
          outcome["冲突跳过"] == 1 and outcome["清理"] == 0, outcome.get("message", ""))
    archive = next(r for r in store.rows("exp_archive") if r.get("归档号") == "AR-R8")
    check("冲突跳过后 R8 未被清理", archive["status"] == "恢复中")
    evidence = store.rows("archive_evidence")[0]
    check("完整副本证据仍可使用", evidence["状态"] == "可使用")
    logs = svc.list_audit("AR-R8")
    purge_logs = [l for l in logs if l["动作"] == "临时归档清理"]
    check("清理冲突写入审计且记录占用方",
          any(l["结果"] == "冲突跳过" and "DL-R8-0001" in l["明细"]
              and "RC-R8-0001" in l["明细"] for l in purge_logs),
          json.dumps(purge_logs, ensure_ascii=False))
    # 办结恢复后再清理：无在途恢复，但仍有校验通过下载占用 -> 仍冲突
    svc.complete_recovery("RC-R8-0001")
    outcome2 = svc.purge_temp_archives()
    check("恢复办结后下载仍占用，清理继续跳过",
          outcome2["冲突跳过"] == 1)
    # 下载与恢复全部解除后，清理可取得源数据
    for d in store.rows("archive_download"):
        if d["办理状态"] == "校验通过":
            d["办理状态"] = "已失效"
    outcome3 = svc.purge_temp_archives()
    check("占用解除后清理取得源数据并清理证据",
          outcome3["清理"] == 1 and store.rows("archive_evidence")[0]["状态"] == "已清理")
    check("清理取得源数据的时间点有审计可回查",
          any(l["结果"] == "已清理" for l in svc.list_audit("AR-R8")
              if l["动作"] == "临时归档清理"))


def test_archive_restore_gate() -> None:
    print("[7] 归档库恢复动作与切片归档恢复维度走同一证据网关")
    store, svc, archive_svc, slice_svc = fresh_setup()
    # 种子在途恢复存在，归档库恢复应被幂等拦截
    r8 = next(r for r in store.rows("exp_archive") if r.get("归档号") == "AR-R8")
    res = archive_svc.restore(int(r8["id"]))
    check("归档库恢复命中在途恢复幂等拦截", res["ok"] is False, res.get("message", ""))
    # 无归档号的旧样例记录不能恢复
    sample = next(r for r in store.rows("exp_archive") if not r.get("归档号")
                  and r["status"] == "已归档")
    res2 = archive_svc.restore(int(sample["id"]))
    check("无归档号/证据的归档拒绝恢复", res2["ok"] is False)
    # 办结在途恢复后，切片分析器对归档恢复维度按归档号复算
    svc.complete_recovery("RC-R8-0001")
    target = next(r for r in store.rows("segment_slice") if r.get("归档号") == "AR-R8")
    # 置回待计算以重跑切片维度分支
    target["status"] = "待计算"
    target["pending"] = True
    run = slice_svc.run_slice(int(target["id"]))
    check("切片分析器按归档号冻结证据复算成功", run["ok"] is True, run.get("message", ""))
    check("切片写回同区间同样本量",
          target["数据窗口"] == "2026-10-11~2026-10-18"
          and "1200" in str(target["样本量"]))
    # 没有归档号的归档恢复切片拒绝计算
    store.rows("segment_slice").append({
        "id": 999, "切片批次": "SG-BAD", "实验编码": "EXP-R8",
        "切片维度": "归档恢复", "切片值": "无归档号", "分组差异": "",
        "status": "待计算", "pending": True, "abnormal": False,
    })
    bad = slice_svc.run_slice(999)
    check("缺归档号的恢复切片不能计算", bad["ok"] is False)


def test_audit_traceability() -> None:
    print("[8] 全流程审计：恢复用了哪份证据、清理何时取得源数据可回查")
    store, svc, _, _ = fresh_setup()
    svc.create_download("AR-R8", "R8-v1", operator="分析员-甲", evidence_id=1)
    svc.complete_recovery("RC-R8-0001")
    logs = svc.list_audit("AR-R8")
    actions = {l["动作"] for l in logs}
    check("审计覆盖下载、恢复、清理三类动作",
          {"打包导出复算包", "发起切片恢复", "办结切片恢复", "临时归档清理"} <= actions,
          str(actions))
    rec_log = next(l for l in logs if l["动作"] == "办结切片恢复")
    check("恢复审计记录所用证据 id=1", rec_log["证据id"] == 1
          and "EV-R8-0001" in rec_log["明细"])
    check("全部审计带时间与归档版次",
          all(l["时间"] and l["归档版次"] == "R8-v1" for l in logs))


def test_generic_action_dispatch() -> None:
    print("[9] 通用资源动作经 run_action 分派到证据网关")
    store, svc, archive_svc, slice_svc = fresh_setup()
    r8 = next(r for r in store.rows("exp_archive") if r.get("归档号") == "AR-R8")
    entry, message = archive_svc.run_action(int(r8["id"]), "恢复实验")
    check("run_action 恢复实验被在途恢复拦截", entry is None and "未产生新恢复" in message, message)
    # 普通维度切片仍走原在线分组逻辑，不受归档改动影响
    store.rows("variant_assign").append({
        "id": 100, "status": "已分配", "分配批次": "B1", "用户标识": "u1",
        "实验编码": "EXP-LIVE", "分组名称": "对照组", "分配时间": "2026-11-01"})
    store.rows("variant_assign").append({
        "id": 101, "status": "已分配", "分配批次": "B1", "用户标识": "u2",
        "实验编码": "EXP-LIVE", "分组名称": "实验组", "分配时间": "2026-11-01"})
    store.rows("segment_slice").append({
        "id": 200, "切片批次": "SG-LIVE", "实验编码": "EXP-LIVE",
        "切片维度": "分组", "切片值": "v", "分组差异": "",
        "status": "待计算", "pending": True, "abnormal": False})
    entry, message = slice_svc.run_action(200, "开始计算")
    check("普通切片动作仍按在线分组计算", entry is not None and entry["status"] == "已计算", message)


if __name__ == "__main__":
    test_seed_tables()
    test_download_verification()
    test_cross_archive_revision_rejected()
    test_recovery_idempotent_and_recompute()
    test_evidence_invalidated_blocks_recovery()
    test_purge_conflict()
    test_archive_restore_gate()
    test_audit_traceability()
    test_generic_action_dispatch()
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
