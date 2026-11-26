# 实验指标分析与归因评估平台

支撑AB实验分组、指标口径计算、显著性检验、归因分析与报告分发的实验评估平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/views/ArchiveRecoveryView.vue 归档复算与切片恢复控制台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装（含归档复算专用接口）
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/archive.py   归档复算专用接口（下载/校验/恢复/清理/审计）
│   ├── app/services/archive_recovery.py 归档证据、复算包与恢复领域逻辑
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   ├── app/store.py          内存数据仓库与示例数据
│   └── tests/                领域场景测试与 R8 端到端 HTTP 测试
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8022/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5202/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8022`。

## 归档复算与切片恢复

实验归档库支持按**归档号 + 归档版次**打包导出复算包，由服务端随包交付检验证据
（检验清单、逐文件 sha256、证据指纹）。核心约束：

- 一次下载只含同一归档号/版次的检验与分组结果，禁止拼接其他实验窗口数据；
- 完整性校验定位到具体文件节（含所属分组）：缺分组文件或哈希/字节校验失败的半包
  标记为「校验失败」，归档详情可回查每次下载的办理结果，半包不得交给恢复；
- 切片恢复打开时读取所引归档号并核查证据是否仍可使用；恢复按归档号关联已校验的
  归档文件，取回冻结检验数据复算，得到**同区间、同样本量**的切片结果；
- 同一归档号已存在在途或已复算的有效恢复时，再次发起幂等拦截，不产生第二份有效
  恢复（文件读取中断后重发也不新建）；
- 临时归档清理若与下载/在途恢复共用同一份源证据则冲突跳过；清理是否取得源数据、
  每次恢复用了哪份证据都写入操作审计，可按归档号回查。

专用接口（前缀 `/api/archive`）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/evidence` | 归档检验证据副本与文件节 |
| POST | `/downloads` | 打包导出复算包并立即完整性校验 |
| GET | `/downloads/{单号}` | 回查一次下载的办理结果（缺件/失败文件节） |
| POST | `/downloads/{单号}/recheck` | 复算包复检（读取中断后再次核查） |
| POST | `/recoveries/check` | 打开恢复时核查归档号对应证据 |
| POST | `/recoveries` | 发起切片恢复（幂等） |
| POST | `/recoveries/{单号}/complete` | 办结在途恢复并复算 |
| POST | `/purge-temp` | 临时归档清理（占用冲突跳过并留痕） |
| GET | `/audit-logs` | 按归档号回查清理与恢复审计 |

测试：`backend/.venv/bin/python -I tests/test_archive_recovery.py`
（领域 50 项）；起服务后 `python -I tests/e2e_r8_http.py`（R8 端到端 17 项）。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 事件接入 | `event_stream` | 事件定义 | 事件编码、事件名称、属性字段 |
| 实验设计 | `exp_design` | 实验方案 | 实验编码、实验名称、分组策略 |
| 分组分配器 | `variant_assign` | 分组结果 | 分配批次、用户标识、实验编码 |
| 指标口径中心 | `metric_catalog` | 指标定义 | 指标编码、指标名称、计算口径 |
| 指标计算作业 | `metric_compute` | 计算作业 | 作业名称、指标编码、计算窗口 |
| 显著性检验 | `significance_test` | 检验结果 | 检验批次、实验编码、指标编码 |
| 归因分析器 | `attr_analysis` | 归因结果 | 归因批次、实验编码、贡献因子 |
| 人群圈选器 | `cohort_builder` | 人群包 | 人群编码、人群名称、圈选条件 |
| 报告生成器 | `report_gen` | 分析报告 | 报告编码、实验编码、报告模板 |
| 报告分发 | `report_dist` | 分发记录 | 分发批次、报告编码、接收方 |
| 窗口调度器 | `window_scheduler` | 调度计划 | 计划名称、指标编码、窗口粒度 |
| 数据时效监控 | `data_freshness` | 时效记录 | 监控对象、数据域、最新水位 |
| 指标异常检测 | `anomaly_metric` | 异常事件 | 异常编号、指标编码、基线区间 |
| 实验归档库 | `exp_archive` | 归档实验 | 实验编码、结束时间、结论摘要 |
| 样本量核对 | `sample_ratio` | 核对结果 | 核对批次、实验编码、计划比例 |
| AA预检器 | `aa_check` | AA检验 | 检验编码、实验编码、检验窗口 |
| 切片分析器 | `segment_slice` | 切片结果 | 切片批次、实验编码、切片维度 |
| 曝光日志接入 | `exposure_log` | 曝光记录 | 日志批次、实验编码、用户标识 |
| 结论评审队列 | `conclusion_review` | 评审记录 | 记录编号、实验编码、评审结论 |
| 指标查询服务 | `metric_api` | 查询接口 | 接口编码、指标编码、服务对象 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
