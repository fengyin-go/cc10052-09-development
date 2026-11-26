/** 归档复算专用接口：复算包下载/完整性回查、证据核查、切片恢复、清理与审计。 */
import { request } from './client'
import type {
  ArchiveActionResult,
  ArchiveAuditLog,
  ArchiveDownload,
  ArchiveEvidence,
  ArchiveRecovery,
} from '@/data/types'

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

async function postJson<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

export function listEvidence(archiveNo?: string): Promise<{ items: ArchiveEvidence[] }> {
  const query = archiveNo ? `?archive_no=${encodeURIComponent(archiveNo)}` : ''
  return getJson(`/api/archive/evidence${query}`)
}

export function listDownloads(archiveNo?: string): Promise<{ items: ArchiveDownload[] }> {
  const query = archiveNo ? `?archive_no=${encodeURIComponent(archiveNo)}` : ''
  return getJson(`/api/archive/downloads${query}`)
}

export function getDownloadResult(downloadNo: string): Promise<ArchiveActionResult<never>> {
  return getJson(`/api/archive/downloads/${encodeURIComponent(downloadNo)}`)
}

export function createDownload(payload: {
  归档号: string
  归档版次: string
  操作人: string
  证据id?: number
}): Promise<ArchiveActionResult<never>> {
  return postJson('/api/archive/downloads', payload)
}

export function recheckDownload(downloadNo: string): Promise<ArchiveActionResult<never>> {
  return postJson(`/api/archive/downloads/${encodeURIComponent(downloadNo)}/recheck`)
}

export function listRecoveries(archiveNo?: string): Promise<{ items: ArchiveRecovery[] }> {
  const query = archiveNo ? `?archive_no=${encodeURIComponent(archiveNo)}` : ''
  return getJson(`/api/archive/recoveries${query}`)
}

export function checkRecoveryEvidence(
  archiveNo: string,
): Promise<ArchiveActionResult<never> & Record<string, unknown>> {
  return postJson('/api/archive/recoveries/check', { 归档号: archiveNo })
}

export function startRecovery(payload: {
  归档号: string
  操作人: string
  下载单号?: string
}): Promise<ArchiveActionResult<ArchiveRecovery>> {
  return postJson('/api/archive/recoveries', payload)
}

export function completeRecovery(
  recoveryNo: string,
): Promise<ArchiveActionResult<ArchiveRecovery>> {
  return postJson(`/api/archive/recoveries/${encodeURIComponent(recoveryNo)}/complete`)
}

export function purgeTempArchives(): Promise<{
  ok: boolean
  message: string
  清理: number
  冲突跳过: number
}> {
  return postJson('/api/archive/purge-temp')
}

export function listAuditLogs(archiveNo?: string): Promise<{ items: ArchiveAuditLog[] }> {
  const query = archiveNo ? `?archive_no=${encodeURIComponent(archiveNo)}` : ''
  return getJson(`/api/archive/audit-logs${query}`)
}
