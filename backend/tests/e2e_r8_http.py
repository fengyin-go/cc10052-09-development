"""R8 叙事的端到端 HTTP 校验（标准库 urllib，对真实运行的服务发请求）。

用法：python -I tests/e2e_r8_http.py
服务：uvicorn app.main:app --host 127.0.0.1 --port 8029
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:8029/api"
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


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 路由层正常情况不抛，留作兜底
        return json.loads(exc.read().decode("utf-8"))


print("[E2E] R8：读取下载 -> 清理冲突 -> 在途恢复 -> 办结复算 -> 审计回查")
# 1. 读取 R8 下载分组明细与证据
ev = call("GET", "/archive/evidence?archive_no=AR-R8")["items"]
check("读到 3 个证据副本，仅完整副本可使用",
      len(ev) == 3 and [e["状态"] for e in ev].count("可使用") == 1)
check("完整文件保留 20:20 检验样本", all("20:20" in e["检验时间"] for e in ev))
dl = call("GET", "/archive/downloads?archive_no=AR-R8")["items"]
check("读到 3 次下载办理结果", len(dl) == 3)
check("半包下载已标明失败，不让交给恢复",
      all(d["办理状态"] == "校验失败" for d in dl if d["下载单号"] in ("DL-R8-0002", "DL-R8-0003")))
passed = next(d for d in dl if d["下载单号"] == "DL-R8-0001")
check("通过的复算包随包交付逐文件哈希与证据指纹",
      passed["证据清单"][0]["sha256"]
      and all(f["期望sha256"] and f["校验通过"] for f in passed["打包文件"]))
# 2. 临时归档清理与同一份源证据冲突
purge = call("POST", "/archive/purge-temp")
check("后台清理冲突跳过，未取得源数据", purge["冲突跳过"] == 1 and purge["清理"] == 0,
      json.dumps(purge, ensure_ascii=False))
# 3. 另一分析员对同一归档号发起恢复 -> 幂等拦截
again = call("POST", "/archive/recoveries", {"归档号": "AR-R8", "操作人": "分析员-丙"})
check("同一归档号重复发起不产生另一份有效恢复",
      again["ok"] is False and again.get("幂等") is True)
rc = call("GET", "/archive/recoveries?archive_no=AR-R8")["items"]
check("始终只有一份恢复单", len(rc) == 1 and rc[0]["恢复单号"] == "RC-R8-0001")
# 4. 截断件不能当作可信证据；尝试用失败下载恢复被拒
bad = call("POST", "/archive/recoveries",
           {"归档号": "AR-R8", "操作人": "分析员-丙", "下载单号": "DL-R8-0002"})
check("失败复算包不能驱动恢复", bad["ok"] is False)
# 5. 在切片分析中办结在途恢复（使用完整副本 EV-R8-0001）
done = call("POST", "/archive/recoveries/RC-R8-0001/complete")
if not done.get("ok"):
    # 脚本重复运行时恢复可能已办结：回读已复算结果，仍校验同区间同样本量。
    stored = next(
        x for x in call("GET", "/archive/recoveries?archive_no=AR-R8")["items"]
        if x["恢复单号"] == "RC-R8-0001")
    check("在途恢复已办结（回读已复算结果）", stored["办理状态"] == "已复算")
    done["ok"] = True
    done["recovery"] = stored
    done["切片结果"] = stored["切片结果"]
else:
    check("在途恢复办结成功", done["recovery"]["办理状态"] == "已复算", done.get("message", ""))
check("复算区间与归档窗口一致",
      all(s["数据窗口"] == "2026-10-11~2026-10-18" for s in done["切片结果"]))
check("复算样本量与冻结一致 1200/1200",
      {s["分组"]: s["样本量"] for s in done["切片结果"]}
      == {"对照组": 1200, "实验组": 1200})
# 6. 切片批次按归档号关联已校验归档文件
slices = call("GET", "/segment_slice?keyword=SG-AR-4")["items"]
check("切片批次关联归档号与证据 id=1",
      len(slices) == 1 and slices[0]["归档号"] == "AR-R8"
      and slices[0]["引用证据id"] == 1 and slices[0]["status"] == "已计算")
# 7. 审计可回查：清理何时取得源数据、恢复用了哪份证据
logs = call("GET", "/archive/audit-logs?archive_no=AR-R8")["items"]
check("清理冲突有审计，记录占用的下载与恢复",
      any(l["动作"] == "临时归档清理" and l["结果"] == "冲突跳过"
          and "DL-R8-0001" in l["明细"] and "RC-R8-0001" in l["明细"]
          and l["证据id"] == 1 for l in logs))
check("办结审计写明使用 EV-R8-0001 复算",
      any(l["动作"] == "办结切片恢复" and l["证据id"] == 1
          and "EV-R8-0001" in l["明细"] for l in logs))
# 8. 归档详情可回查每次下载的缺件/失败文件节
detail = call("GET", "/archive/downloads/DL-R8-0003")
check("回查缺件定位到实验组文件节",
      detail["download"]["缺件"][0]["文件"] == "groups/实验组.parquet"
      and detail["download"]["缺件"][0]["分组"] == "实验组")
# 9. 办结后再次发起恢复仍不得产生第二份有效恢复
once_more = call("POST", "/archive/recoveries", {"归档号": "AR-R8", "操作人": "分析员-丙"})
check("办结后重发仍被拦截", once_more["ok"] is False
      and len(call("GET", "/archive/recoveries?archive_no=AR-R8")["items"]) == 1)

print(f"\nE2E 结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
