/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 归档检验证据副本（按归档号+版次冻结，含文件节与分组/检验结果）。 */
export type ArchiveEvidence = {
  证据id: number
  证据编码: string
  归档号: string
  归档版次: string
  实验编码: string
  副本类型: string
  状态: string
  检验时间: string
  数据窗口: string
  sha256: string
  文件节: { 文件: string; 分组: string; 问题: string }[]
  分组结果: { 分组名称: string; 样本量: number; 分组均值: string }[]
  检验结果: { 指标编码: string; P值: string; 置信区间: string }[]
}

export type PackageFile = {
  文件: string
  分组: string
  期望sha256: string
  实际sha256: string
  期望字节: number
  实际字节: number
  问题: string
  校验通过: boolean
}

/** 一次复算包下载的办理结果，缺件与校验失败文件定位到具体文件节。 */
export type ArchiveDownload = {
  id: number
  下载单号: string
  归档号: string
  归档版次: string
  实验编码: string
  证据id: number
  操作人: string
  发起时间: string
  办理状态: string
  打包文件: PackageFile[]
  缺件: { 文件: string; 分组: string; 问题: string }[]
  校验失败文件: { 文件: string; 分组: string; 问题: string }[]
  证据清单: { 证据编码: string; 副本类型: string; 检验时间: string; sha256: string }[]
}

export type SliceRecomputeResult = {
  指标编码: string
  切片维度: string
  分组: string
  数据窗口: string
  样本量: number
  分组均值: string
  组间差异: number
  P值: string
  置信区间: string
}

/** 切片恢复单：按归档号关联已校验归档文件，记录所用证据与复算结果。 */
export type ArchiveRecovery = {
  id: number
  恢复单号: string
  归档号: string
  归档版次: string
  实验编码: string
  下载单号: string
  证据id: number
  操作人: string
  发起时间: string
  办结时间: string
  办理状态: string
  切片批次: string
  数据窗口: string
  样本量: Record<string, number>
  切片结果: SliceRecomputeResult[]
  失败原因: string
}

export type ArchiveAuditLog = {
  id: number
  时间: string
  动作: string
  归档号: string
  归档版次: string
  证据id: number | null
  下载单号: string
  恢复单号: string
  操作人: string
  结果: string
  明细: string
}

export type ArchiveActionResult<T> = {
  ok: boolean
  message: string
  幂等?: boolean
  download?: ArchiveDownload
  recovery?: ArchiveRecovery
  items?: T[]
}
