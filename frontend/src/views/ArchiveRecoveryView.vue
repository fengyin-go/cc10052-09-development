<template>
  <section class="page archive-console" data-module="archive-recovery">
    <header class="page-head">
      <div>
        <h2>归档复算与切片恢复</h2>
        <p class="page-desc">
          按归档号打包导出复算包并随包交付检验证据；完整性校验不通过的半包不得交给恢复，
          切片恢复只引用同一归档版次仍可使用的冻结证据。
        </p>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="loadAll">
      <label class="filter-item">
        <span>归档号</span>
        <input v-model="archiveNo" data-testid="archive-no" placeholder="如 AR-R8" required />
      </label>
      <button class="btn primary" data-testid="load-archive" type="submit">读取归档</button>
      <button class="btn ghost" type="button" @click="runPurge">后台清理临时归档</button>
    </form>

    <p v-if="errorMessage" class="error-text" data-testid="console-error">{{ errorMessage }}</p>
    <p v-if="infoMessage" class="info-text" data-testid="console-info">{{ infoMessage }}</p>

    <div class="archive-grid">
      <article class="panel">
        <h3>归档检验证据</h3>
        <p class="panel-tip">证据按归档号 + 版次冻结，截断/缺件副本不可作为可信证据。</p>
        <table class="data-table">
          <thead>
            <tr><th>证据编码</th><th>副本类型</th><th>状态</th><th>检验时间</th><th>文件节</th></tr>
          </thead>
          <tbody>
            <tr v-for="e in evidence" :key="e.证据id" :data-testid="`evidence-${e.证据id}`">
              <td>{{ e.证据编码 }}</td>
              <td>{{ e.副本类型 }}</td>
              <td>
                <span :class="['status-tag', e.状态 === '可使用' ? 'ok' : 'bad']">{{ e.状态 }}</span>
              </td>
              <td>{{ e.检验时间 }}</td>
              <td>{{ e.文件节.length }} 节</td>
            </tr>
            <tr v-if="!evidence.length"><td colspan="5" class="empty-state">先读取归档号</td></tr>
          </tbody>
        </table>
        <div v-if="evidence.length" class="evidence-detail">
          <strong>冻结分组与样本量：</strong>
          <span v-for="g in evidence[0].分组结果" :key="g.分组名称" class="sample-chip">
            {{ g.分组名称 }} {{ g.样本量 }}（均值 {{ g.分组均值 }}）
          </span>
          <p class="panel-tip">数据窗口：{{ evidence[0].数据窗口 }}</p>
        </div>
      </article>

      <article class="panel">
        <h3>复算包下载与完整性</h3>
        <form class="inline-form" @submit.prevent="onDownload">
          <label class="filter-item">
            <span>归档版次</span>
            <input v-model="revision" data-testid="revision" placeholder="如 R8-v1" required />
          </label>
          <label class="filter-item">
            <span>证据id</span>
            <input v-model.number="evidenceId" data-testid="evidence-id" type="number" min="1" />
          </label>
          <button class="btn primary" data-testid="create-download" type="submit">打包导出并校验</button>
        </form>
        <table class="data-table">
          <thead>
            <tr><th>下载单号</th><th>证据</th><th>办理状态</th><th>问题文件节</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="d in downloads" :key="d.id" :data-testid="`download-${d.下载单号}`">
              <td>{{ d.下载单号 }}</td>
              <td>{{ d.证据id }}</td>
              <td>
                <span :class="['status-tag', d.办理状态 === '校验通过' ? 'ok' : 'bad']">
                  {{ d.办理状态 }}
                </span>
              </td>
              <td>
                <ul v-if="d.缺件.length || d.校验失败文件.length" class="issue-list">
                  <li v-for="m in d.缺件" :key="`miss-${m.文件}`" class="issue bad">
                    缺件：{{ m.文件 }}（分组：{{ m.分组 }}）
                  </li>
                  <li v-for="f in d.校验失败文件" :key="`fail-${f.文件}`" class="issue bad">
                    校验失败：{{ f.文件 }}（分组：{{ f.分组 }}）
                  </li>
                </ul>
                <span v-else class="ok">全部文件节哈希一致</span>
              </td>
              <td>
                <button class="link" type="button" @click="onRecheck(d.下载单号)">复检</button>
              </td>
            </tr>
            <tr v-if="!downloads.length"><td colspan="5" class="empty-state">暂无下载记录</td></tr>
          </tbody>
        </table>
        <details v-for="d in downloads" :key="`files-${d.id}`" class="file-details">
          <summary>{{ d.下载单号 }} 逐文件校验明细</summary>
          <table class="data-table compact">
            <thead>
              <tr><th>文件节</th><th>分组</th><th>期望/实际字节</th><th>校验</th></tr>
            </thead>
            <tbody>
              <tr v-for="f in d.打包文件" :key="f.文件">
                <td>{{ f.文件 }}</td>
                <td>{{ f.分组 }}</td>
                <td>{{ f.期望字节 }} / {{ f.实际字节 }}</td>
                <td>
                  <span :class="['status-tag', f.校验通过 ? 'ok' : 'bad']">
                    {{ f.校验通过 ? '通过' : f.问题 }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </details>
      </article>
    </div>

    <div class="archive-grid">
      <article class="panel">
        <h3>切片恢复（按归档号关联证据）</h3>
        <div class="inline-form">
          <button class="btn" data-testid="check-evidence" type="button" @click="onCheck">
            核查证据是否可用
          </button>
          <button class="btn primary" data-testid="start-recovery" type="button" @click="onStart">
            发起恢复
          </button>
        </div>
        <table class="data-table">
          <thead>
            <tr><th>恢复单号</th><th>状态</th><th>引用证据</th><th>复算包</th><th>区间/样本量</th><th>办结</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in recoveries" :key="r.id" :data-testid="`recovery-${r.恢复单号}`">
              <td>{{ r.恢复单号 }}</td>
              <td>
                <span :class="['status-tag', r.办理状态 === '已复算' ? 'ok' : r.办理状态 === '复算失败' ? 'bad' : 'pending']">
                  {{ r.办理状态 }}
                </span>
              </td>
              <td>EV id {{ r.证据id }}</td>
              <td>{{ r.下载单号 }}</td>
              <td>{{ r.数据窗口 }} / {{ formatSamples(r.样本量) }}</td>
              <td>
                <button
                  v-if="r.办理状态 === '在途恢复'"
                  class="link"
                  type="button"
                  @click="onComplete(r.恢复单号)"
                >
                  办结复算
                </button>
                <span v-else>{{ r.办结时间 ? '已办结' : r.失败原因 }}</span>
              </td>
            </tr>
            <tr v-if="!recoveries.length"><td colspan="6" class="empty-state">暂无恢复记录</td></tr>
          </tbody>
        </table>
        <details v-for="r in recoveries.filter((x) => x.切片结果.length)" :key="`slice-${r.id}`" class="file-details">
          <summary>{{ r.恢复单号 }} 复算切片结果（同区间同样本量）</summary>
          <table class="data-table compact">
            <thead>
              <tr><th>指标</th><th>分组</th><th>样本量</th><th>组间差异</th><th>P值</th></tr>
            </thead>
            <tbody>
              <tr v-for="s in r.切片结果" :key="`${s.指标编码}-${s.分组}`">
                <td>{{ s.指标编码 }}</td>
                <td>{{ s.分组 }}</td>
                <td>{{ s.样本量 }}</td>
                <td>{{ s.组间差异 }}</td>
                <td>{{ s.P值 }}</td>
              </tr>
            </tbody>
          </table>
        </details>
      </article>

      <article class="panel">
        <h3>操作审计（可回查）</h3>
        <p class="panel-tip">后台清理何时取得源数据、每次恢复用了哪份证据均留痕。</p>
        <table class="data-table compact">
          <thead>
            <tr><th>时间</th><th>动作</th><th>结果</th><th>证据</th><th>明细</th></tr>
          </thead>
          <tbody>
            <tr v-for="log in auditLogs" :key="log.id" :data-testid="`audit-${log.id}`">
              <td>{{ formatTime(log.时间) }}</td>
              <td>{{ log.动作 }}</td>
              <td>
                <span :class="['status-tag', log.结果 === '成功' || log.结果 === '通过' || log.结果 === '已清理' ? 'ok' : log.结果.includes('失败') ? 'bad' : 'pending']">
                  {{ log.结果 }}
                </span>
              </td>
              <td>{{ log.证据id ?? '—' }}</td>
              <td class="audit-detail">{{ log.明细 }}</td>
            </tr>
            <tr v-if="!auditLogs.length"><td colspan="5" class="empty-state">暂无审计记录</td></tr>
          </tbody>
        </table>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import {
  checkRecoveryEvidence,
  completeRecovery,
  createDownload,
  listAuditLogs,
  listDownloads,
  listEvidence,
  listRecoveries,
  purgeTempArchives,
  recheckDownload,
  startRecovery,
} from '@/api/archive-service'
import type {
  ArchiveAuditLog,
  ArchiveDownload,
  ArchiveEvidence,
  ArchiveRecovery,
} from '@/data/types'

const archiveNo = ref('AR-R8')
const revision = ref('R8-v1')
const evidenceId = ref<number | null>(1)
const evidence = ref<ArchiveEvidence[]>([])
const downloads = ref<ArchiveDownload[]>([])
const recoveries = ref<ArchiveRecovery[]>([])
const auditLogs = ref<ArchiveAuditLog[]>([])
const errorMessage = ref('')
const infoMessage = ref('')

function formatSamples(samples: Record<string, number>): string {
  return Object.entries(samples).map(([name, size]) => `${name} ${size}`).join(' / ')
}

function formatTime(value: string): string {
  return value.replace('T', ' ').replace('Z', '')
}

function fail(error: unknown, fallback: string) {
  errorMessage.value = error instanceof Error ? error.message : fallback
  infoMessage.value = ''
}

async function loadAll() {
  errorMessage.value = ''
  if (!archiveNo.value.trim()) {
    errorMessage.value = '请先输入归档号'
    return
  }
  try {
    const [ev, dl, rc, logs] = await Promise.all([
      listEvidence(archiveNo.value.trim()),
      listDownloads(archiveNo.value.trim()),
      listRecoveries(archiveNo.value.trim()),
      listAuditLogs(archiveNo.value.trim()),
    ])
    evidence.value = ev.items
    downloads.value = dl.items
    recoveries.value = rc.items
    auditLogs.value = logs.items
    if (evidence.value.length) revision.value = evidence.value[0].归档版次
    infoMessage.value = `已读取归档 ${archiveNo.value}：${evidence.value.length} 份证据、`
      + `${downloads.value.length} 次下载、${recoveries.value.length} 次恢复`
  } catch (error) {
    fail(error, '归档数据读取失败')
  }
}

async function onDownload() {
  errorMessage.value = ''
  try {
    const result = await createDownload({
      归档号: archiveNo.value.trim(),
      归档版次: revision.value.trim(),
      操作人: '前端分析员',
      ...(evidenceId.value ? { 证据id: evidenceId.value } : {}),
    })
    infoMessage.value = result.message
    if (!result.ok) errorMessage.value = result.message
    await loadAll()
  } catch (error) {
    fail(error, '复算包下载失败')
  }
}

async function onRecheck(downloadNo: string) {
  errorMessage.value = ''
  try {
    const result = await recheckDownload(downloadNo)
    infoMessage.value = result.message
    if (!result.ok) errorMessage.value = result.message
    await loadAll()
  } catch (error) {
    fail(error, '复算包复检失败')
  }
}

async function onCheck() {
  errorMessage.value = ''
  try {
    const result = await checkRecoveryEvidence(archiveNo.value.trim())
    infoMessage.value = result.message
    if (!result.ok) errorMessage.value = result.message
  } catch (error) {
    fail(error, '证据核查失败')
  }
}

async function onStart() {
  errorMessage.value = ''
  try {
    const result = await startRecovery({
      归档号: archiveNo.value.trim(),
      操作人: '前端分析员',
    })
    infoMessage.value = result.message
    if (!result.ok) errorMessage.value = result.message
    await loadAll()
  } catch (error) {
    fail(error, '发起恢复失败')
  }
}

async function onComplete(recoveryNo: string) {
  errorMessage.value = ''
  try {
    const result = await completeRecovery(recoveryNo)
    infoMessage.value = result.message
    if (!result.ok) errorMessage.value = result.message
    await loadAll()
  } catch (error) {
    fail(error, '恢复办结失败')
  }
}

async function runPurge() {
  errorMessage.value = ''
  try {
    const result = await purgeTempArchives()
    infoMessage.value = result.message
    await loadAll()
  } catch (error) {
    fail(error, '临时归档清理失败')
  }
}

loadAll()
</script>
