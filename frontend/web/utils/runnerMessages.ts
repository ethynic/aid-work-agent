import type { ChatMessage, ProgressMessage, DownloadableFile, ImageRef } from '@/types'
import type { ChatMessageRecord } from '@/api/session'
import type { RunnerView, RunnerPresentation } from '@/api/runner'
import { extractQuickOptions } from './quickOptions'

const time = (value: unknown, fallback: number) => {
  const parsed = typeof value === 'number' ? value : typeof value === 'string' ? Date.parse(value) : NaN
  return Number.isFinite(parsed) ? parsed : fallback
}

/** One mapper for cumulative public facts and persisted legacy display events. */
export function progressMessages(values: Record<string, unknown>[] | undefined, timestamp: number): ProgressMessage[] {
  return (values || []).flatMap(value => {
    const type = value.type as ProgressMessage['type']
    if (!['progress', 'complete', 'error', 'thinking', 'tool_start', 'tool_result'].includes(type)) return []
    const name = String(value.displayName || value.toolName || '')
    let content = String(value.content || value.data || '')
    if (type === 'tool_start' && !content) content = `🔧 需要调用工具【${name}】`
    if (type === 'tool_result' && !content) content = value.success ? `✅ 【${name}】执行完成` : `❌ ${name}执行失败`
    if (type === 'progress' && /^(🔧\s*正在执行|✅\s*.+执行完成$)/.test(content.trim())) return []
    return [{ type, content, timestamp: time(value.timestamp, timestamp),
      toolName: value.toolName as string | undefined, displayName: value.displayName as string | undefined,
      toolCallId: value.toolCallId as string | undefined, toolArgs: value.toolArgs as object | undefined,
      result: value.result, success: value.success as boolean | undefined }]
  })
}

function presentation(value: RunnerPresentation, timestamp: number): Partial<ChatMessage> {
  const progress = progressMessages(value.progressMessages, timestamp)
  const files = new Map<string, DownloadableFile>((value.downloadableFiles || []).map(file => [file.file_id, file]))
  let options = value.quickOptions
  for (const event of progress) {
    if (event.type !== 'tool_result' || !event.success) continue
    const result = event.result
    if (result?.file_id && result.visible !== false && !files.has(result.file_id)) files.set(result.file_id, {
      file_id: result.file_id, file_name: result.download_file_name || result.file_name || '未命名文件',
      file_size: result.file_size || 0, download_url: result.download_url || `/api/files/${result.file_id}/download`,
      mime_type: result.mime_type || '',
    })
    const next = extractQuickOptions(result)
    if (next.length) options = next
  }
  return { progressMessages: progress, downloadableFiles: Array.from(files.values()),
    verboseMessages: value.verboseMessages || [], quickOptions: options, browserAssistance: value.browserAssistance }
}

export function historyMessages(records: ChatMessageRecord[]): ChatMessage[] {
  return records.filter(row => ['user', 'assistant'].includes(row.role) && !(row.role === 'assistant' && row.metadata?.tool_calls))
    .map(row => {
      const meta = row.metadata || {}
      const timestamp = time(row.role === 'user' ? meta.accepted_at || meta.sent_at || row.created_at : row.created_at, Date.now())
      return { role: row.role as ChatMessage['role'], content: row.content, timestamp, messageId: row.message_id,
        runnerId: meta.runner_id, queueOrder: meta.runner_queue_order,
        ...presentation(meta, timestamp), attachments: meta.attachments, images: meta.images,
        cancelled: meta.cancelled === true }
    })
}

export function runnerMessages(row: RunnerView): ChatMessage[] {
  const accepted = time(row.accepted_at, Date.now())
  const finished = time(row.finished_at || row.updated_at, accepted)
  const snapshot = row.snapshot
  const display = { ...(row.result?.assistant_metadata || {}), ...snapshot }
  const images: ImageRef[] = (row.result?.images || snapshot.images || []).map(image => ({ ...image,
    placement: image.placement || snapshot.imagesPlacement || 'after_text' }))
  const common = { runnerId: row.runner_id, clientRequestId: row.client_request_id, queueOrder: row.queue_order }
  const details = presentation(display, accepted)
  // The current safe snapshot may intentionally remove an unproved old card.
  // Finalization metadata cannot recreate it on the cumulative runner view.
  details.browserAssistance = snapshot.browserAssistance
  let wait = snapshot.waiting
  let depth = 64
  while (wait?.kind === 'child_wait' && depth-- > 0) wait = wait.child_wait
  if (!['completed', 'failed', 'cancelled'].includes(row.status)
      && wait?.kind === 'verification' && typeof wait.question === 'string') {
    details.waitingNotice = wait.question
  }
  if (['completed', 'failed', 'cancelled'].includes(row.status) && !details.progressMessages?.some(item => ['complete', 'error'].includes(item.type))) {
    details.progressMessages?.push({ type: row.status === 'completed' ? 'complete' : 'error', timestamp: finished,
      content: row.status === 'completed' ? '✅ 任务完成' : row.status === 'cancelled' ? '⚠️ 已停止' : `❌ ${row.result?.error_code || '执行失败'}` })
  }
  const inputs = snapshot.supplementalInputs || []
  const additions: ChatMessage[] = []
  for (const question of snapshot.clarificationQuestions || []) {
    additions.push({ ...common,role:'assistant',messageId:question.message_id,
      content:question.text,timestamp:time(question.created_at,accepted) })
    for (const input of inputs.filter(item => item.wait_id===question.wait_id)) {
      additions.push({ ...common,role:'user',messageId:input.message_id,
        clientRequestId:input.client_request_id,content:input.text,attachments:input.attachments,
        timestamp:time(input.accepted_at,accepted) })
    }
  }
  for (const input of inputs) {
    if (!additions.some(item => item.messageId===input.message_id)) additions.push({ ...common,role:'user',
      messageId:input.message_id,clientRequestId:input.client_request_id,content:input.text,
      attachments:input.attachments,timestamp:time(input.accepted_at,accepted) })
  }
  return [
    { ...common, role: 'user', messageId: snapshot.input?.message_id || row.runner_id + ':user',
      content: snapshot.input?.text || '', attachments: snapshot.input?.attachments, timestamp: accepted },
    ...additions,
    { ...common, role: 'assistant', messageId: row.runner_id + ':assistant',
      content: row.result?.output ?? snapshot.output ?? '', timestamp: finished,
      ...details, images, cancelled: row.status === 'cancelled',
      runnerActive: !['completed', 'failed', 'cancelled'].includes(row.status) },
  ]
}

/** Display times never reorder a turn. Stable runner IDs replace optimistic/history copies. */
export function mergeRunnerMessages(history: ChatMessage[], rows: RunnerView[]): ChatMessage[] {
  const groups: { id: string; order?: number; messages: ChatMessage[] }[] = []
  const byId = new Map<string, typeof groups[number]>()
  for (const message of history) {
    const id = message.runnerId || (message.clientRequestId ? 'request:' + message.clientRequestId : message.messageId)
    // Legacy/optimistic messages without an identity keep their existing order.
    const key = id || 'legacy:' + groups.length
    let group = byId.get(key)
    if (!group) { group = { id: key, order: message.queueOrder, messages: [] }; groups.push(group); byId.set(key, group) }
    if (!group.messages.some(item => item.messageId && item.messageId===message.messageId)) group.messages.push(message)
  }
  for (const row of [...rows].sort((a, b) => a.queue_order - b.queue_order)) {
    let group = byId.get(row.runner_id) || byId.get('request:' + row.client_request_id)
    const projected = runnerMessages(row)
    if (group) {
      // History's physical message ID stays authoritative after finalization.
      for (const message of projected) {
        const persisted = group.messages.find(item => item.messageId===message.messageId)
          || (message.messageId===row.runner_id+':assistant'
            ? [...group.messages].reverse().find(item => item.role==='assistant' && !item.messageId?.startsWith('wait_')) : undefined)
        if (persisted?.messageId && !persisted.messageId.startsWith('request:')) message.messageId = persisted.messageId
      }
      group.messages = projected; group.id = row.runner_id; group.order = row.queue_order
    } else {
      group = { id: row.runner_id, order: row.queue_order, messages: projected }
      const before = groups.findIndex(item => item.order !== undefined && item.order > row.queue_order)
      if (before >= 0) groups.splice(before, 0, group)
      else groups.push(group)
    }
    byId.set(row.runner_id, group)
  }
  // Existing history may have loaded between two queued accepts. Reorder known
  // runner groups by queue_order, leaving legacy group positions intact.
  const ordered = groups.filter(group => group.order !== undefined).sort((a, b) => a.order! - b.order!)
  let index = 0
  return groups.flatMap(group => group.order === undefined ? group.messages : ordered[index++].messages)
}
