/**
 * Renderer 应用逻辑 —— 通过 window.associationClient IPC 桥与主进程通信。
 */

// eslint-disable-next-line no-undef
const client = window.associationClient

// 全局错误捕获，方便调试
window.addEventListener('error', (e) => {
  console.error('[renderer error]', e.message, e.error?.stack || '')
})
window.addEventListener('unhandledrejection', (e) => {
  console.error('[unhandled promise]', e.reason)
})

// ============== 状态 ==============

const state = {
  config: null,
  cliRunning: false,
  stopping: false,  // 已点击停止、等待 CLI 退出（优雅停止或强杀）
  associations: [],
  progressMap: new Map(),
  totalConsumed: 0,
  lastOutput: '',
  currentOutputPath: '',  // 本次任务的 Excel 输出路径（强杀时用于提示 partial 增量文件）
  inputFilePath: '',  // 上传的输入文件路径
  guiLog: [],         // GUI 内存日志（{time,level,message}），供导出诊断包
}

// ============== DOM 元素 ==============

const $ = (id) => document.getElementById(id)

const pages = {
  activation: $('page-activation'),
  collect: $('page-collect'),
}

// ============== 页面切换 ==============

function showPage(name) {
  Object.values(pages).forEach((p) => p.classList.add('hidden'))
  pages[name].classList.remove('hidden')
}

// ============== 初始化 ==============

async function init() {
  console.log('[init] loading config...')
  state.config = await client.config.load()
  console.log('[init] config loaded:', state.config ? 'has config' : 'no config')
  if (state.config) {
    showPage('collect')
    console.log('[init] refreshing credits...')
    await refreshCredits()
    console.log('[init] credits refreshed')
  } else {
    showPage('activation')
  }

  client.cli.onEvent(handleCliEvent)
  client.cli.onClose(handleCliClose)
  bindEvents()
  console.log('[init] events bound, ready')
}

// ============== 激活 ==============

async function handleActivate() {
  const code = $('activation-code').value.trim()
  const serverUrl = $('server-url').value.trim()
  const clientName = $('client-name').value.trim()

  const errEl = $('activation-error')
  errEl.classList.add('hidden')

  if (!code) {
    errEl.textContent = '请输入激活码'
    errEl.classList.remove('hidden')
    return
  }

  $('btn-activate').disabled = true
  $('btn-activate').textContent = '激活中...'

  try {
    const result = await client.cli.activate(code, serverUrl, clientName)
    if (!result.ok) throw new Error(result.error || '激活失败')
    const data = result.data
    await client.config.save({
      bindingId: data.binding_id,
      accessToken: data.access_token || '',
      tenantId: data.tenant_id,
      tenantName: data.tenant_name || '',
      serverUrl,
      activatedAt: new Date().toISOString(),
    })
    state.config = await client.config.load()
    showPage('collect')
    await refreshCredits()
  } catch (err) {
    errEl.textContent = err.message || '激活失败'
    errEl.classList.remove('hidden')
  } finally {
    $('btn-activate').disabled = false
    $('btn-activate').textContent = '激活'
  }
}

// ============== 积分 ==============

async function refreshCredits() {
  if (!state.config) return
  try {
    const result = await client.cli.getCredits(state.config.serverUrl, state.config.accessToken)
    if (result.ok && result.data) {
      updateBalance(parseFloat(result.data.balance || 0))
    }
  } catch (err) {
    console.error('查询积分失败:', err)
  }
}

function updateBalance(balance) {
  $('credits-balance').textContent = balance.toFixed(2)
  $('credits-display').classList.remove('hidden')
  if (balance <= 0) {
    $('balance-warning').classList.remove('hidden')
    $('btn-start').disabled = true
  } else {
    $('balance-warning').classList.add('hidden')
    $('btn-start').disabled = false
  }
}

// ============== 收集 ==============

function parseAssociations(text) {
  return text.split(/[,\n，；;]/).map((s) => s.trim()).filter((s) => s.length > 0)
}

async function handleStart() {
  const text = $('associations-input').value.trim()
  const associations = parseAssociations(text)
  const hasFile = !!state.inputFilePath
  const hasText = associations.length > 0

  if (!hasText && !hasFile) {
    alert('请输入协会名称或上传文件')
    return
  }

  // 输出路径自动生成到桌面
  const desktopPath = await client.system.getDesktopPath()
  const now = new Date()
  const ts = `${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}_${String(now.getHours()).padStart(2,'0')}${String(now.getMinutes()).padStart(2,'0')}${String(now.getSeconds()).padStart(2,'0')}`
  const outputPath = `${desktopPath}\\协会收集结果_${ts}.xlsx`
  state.currentOutputPath = outputPath

  // 重置状态
  state.associations = hasText ? associations : []  // 文件模式时 count 由 start 事件补全
  state.progressMap.clear()
  state.totalConsumed = 0
  $('progress-list').innerHTML = ''
  $('detail-list').innerHTML = ''
  $('log-stream').innerHTML = ''
  $('current-task').classList.add('hidden')
  $('progress-counter').textContent = hasText ? `0 / ${associations.length}` : '读取中...'
  $('progress-section').classList.remove('hidden')
  $('result-section').classList.add('hidden')
  $('btn-start').classList.add('hidden')
  $('btn-stop').classList.remove('hidden')
  resetStopButton()
  state.stopping = false
  $('progress-status').textContent = '运行中'
  $('progress-status').className = 'log-status running'

  state.cliRunning = true

  if (hasFile) {
    // 文件输入模式
    await client.cli.collect([], outputPath, state.config.serverUrl, state.config.accessToken, state.inputFilePath)
  } else {
    await client.cli.collect(associations, outputPath, state.config.serverUrl, state.config.accessToken)
  }
}

function resetStopButton() {
  const btn = $('btn-stop')
  btn.disabled = false
  btn.textContent = '停止'
}

function handleStop() {
  if (!state.cliRunning || state.stopping) return
  state.stopping = true
  // 立即反馈：停止可能要先等 CLI 保存部分结果（优雅停止宽限期）
  const btn = $('btn-stop')
  btn.disabled = true
  btn.textContent = '正在停止…'
  appendLog('INFO', '正在停止任务：已完成的协会结果会被保留，请稍候…')
  client.cli.kill()
}

// ============== CLI 事件处理 ==============

function handleCliEvent(evt) {
  console.log('[app] event received:', evt.event, evt.message || evt.credit_cost || '')
  switch (evt.event) {
    case 'start':
      appendLog('INFO', `开始收集 ${evt.associations?.length || 0} 个协会`)
      break
    case 'progress':
      handleProgressEvent(evt)
      break
    case 'billing':
      handleBillingEvent(evt)
      break
    case 'log':
      appendLog(evt.level || 'INFO', evt.message || '', evt.association || '')
      break
    case 'error':
      appendLog('ERROR', `[${evt.association || ''}] ${evt.message || evt.error_code}`)
      if (evt.session_fatal) {
        appendLog('ERROR', '任务终止')
        $('log-status').textContent = '错误'
        $('log-status').className = 'log-status error'
      }
      break
    case 'complete':
      handleCompleteEvent(evt)
      break
    case 'stopped':
      handleStoppedEvent(evt)
      break
  }
}

function handleProgressEvent(evt) {
  const name = evt.association || ''
  if (!name) return

  let item = state.progressMap.get(name)
  if (!item) {
    const index = state.progressMap.size + 1
    item = { steps: [], status: 'running', index, element: null }
    state.progressMap.set(name, item)
    item.element = createProgressItem(name, index, state.associations.length)
    $('progress-list').appendChild(item.element)
    $('progress-counter').textContent = `${index} / ${state.associations.length}`
  }

  if (evt.status === 'success') item.status = 'success'
  else if (evt.status === 'failed') item.status = 'failed'

  if (evt.step && evt.message) {
    item.steps.push({ step: evt.step, message: evt.message, status: evt.status })
    // 右侧进度详情面板显示
    appendDetail(name, evt.message, evt.status)
  }

  // 更新当前任务状态
  updateCurrentTask(name, evt.message || '')
  updateProgressItem(item)
}

function handleBillingEvent(evt) {
  // 过程中不显示积分消耗，只在完成时显示总数
  if (evt.balance_after != null) {
    updateBalance(parseFloat(evt.balance_after))
  }
}

function handleCompleteEvent(evt) {
  state.cliRunning = false
  state.stopping = false
  $('btn-start').classList.remove('hidden')
  $('btn-stop').classList.add('hidden')
  resetStopButton()
  $('progress-status').textContent = '完成'
  $('progress-status').className = 'log-status success'

  const summary = evt.summary || {}
  const totalCost = parseFloat(evt.total_consumed || 0).toFixed(2)
  $('result-summary').innerHTML = `
    <div class="result-stat success"><div class="stat-num">${summary.complete || 0}</div><div class="stat-label">完整成功</div></div>
    <div class="result-stat partial"><div class="stat-num">${summary.partial || 0}</div><div class="stat-label">部分成功</div></div>
    <div class="result-stat failed"><div class="stat-num">${summary.failed || 0}</div><div class="stat-label">失败</div></div>
    <div class="result-stat cost"><div class="stat-num">${totalCost}</div><div class="stat-label">消耗积分</div></div>
  `
  // 显示输出路径
  if (evt.output) {
    state.lastOutput = evt.output
    $('result-output-path').textContent = evt.output
    $('result-output').classList.remove('hidden')
  }
  $('result-section').classList.remove('hidden')
  // 结果区已在进度上方；批量上传进度很长时，完成时滚到顶部让结果立即可见
  $('result-section').scrollIntoView({ behavior: 'smooth', block: 'start' })
  // 自动清空输入（协会名称文本 / 上传文件），防止共享机器上他人误点「开始收集」重复跑
  $('associations-input').value = ''
  $('input-file-name').textContent = ''
  state.inputFilePath = ''
  appendLog('INFO', `✅ 收集完成，共消耗 ${parseFloat(evt.total_consumed || 0).toFixed(2)} 积分`)
  refreshCredits()
}

// 用户停止：CLI 优雅收尾后发来 completed/failed/remaining 名单 + 部分结果 Excel 路径
function handleStoppedEvent(evt) {
  state.cliRunning = false
  state.stopping = false
  $('btn-start').classList.remove('hidden')
  $('btn-stop').classList.add('hidden')
  resetStopButton()
  $('progress-status').textContent = '已停止'
  $('progress-status').className = 'log-status'

  const completed = evt.completed || []
  const failed = evt.failed || []
  const remaining = evt.remaining || []
  const total = completed.length + failed.length + remaining.length

  // 未处理协会在进度区标记为「已停止」（还没出现过 progress 事件的先补建行）
  for (const name of remaining) {
    let item = state.progressMap.get(name)
    if (!item) {
      const index = state.progressMap.size + 1
      item = { steps: [], status: 'stopped', index, element: null }
      state.progressMap.set(name, item)
      item.element = createProgressItem(name, index, total || state.associations.length)
      $('progress-list').appendChild(item.element)
    }
    item.status = 'stopped'
    updateProgressItem(item)
  }

  $('result-summary').innerHTML = `
    <div class="result-stat success"><div class="stat-num">${completed.length}</div><div class="stat-label">已完成</div></div>
    <div class="result-stat failed"><div class="stat-num">${failed.length}</div><div class="stat-label">失败</div></div>
    <div class="result-stat partial"><div class="stat-num">${remaining.length}</div><div class="stat-label">未处理</div></div>
  `
  if (evt.output) {
    state.lastOutput = evt.output
    $('result-output-path').textContent = evt.output
    $('result-output').classList.remove('hidden')
  }
  $('result-section').classList.remove('hidden')
  $('result-section').scrollIntoView({ behavior: 'smooth', block: 'start' })

  // 未处理名单填回输入框（清掉文件选择），方便用户直接补跑
  if (remaining.length > 0) {
    $('associations-input').value = remaining.join('\n')
    $('input-file-name').textContent = ''
    state.inputFilePath = ''
  }
  appendLog('INFO', `⏹ 任务已停止：完成 ${completed.length}/${total} 个，部分结果已保存${evt.output ? '：' + evt.output : ''}`)
  refreshCredits()
}

function handleCliClose(code) {
  state.cliRunning = false
  const wasStopping = state.stopping
  state.stopping = false
  $('btn-start').classList.remove('hidden')
  $('btn-stop').classList.add('hidden')
  resetStopButton()
  if (wasStopping) {
    // 用户主动停止：优雅停止时 stopped 事件已先展示结果；
    // 强杀（taskkill）时退出码可能非 0/2，也按「已停止」归类，不报异常退出
    if ($('progress-status').textContent !== '已停止') {
      $('progress-status').textContent = '已停止'
      $('progress-status').className = 'log-status'
      appendLog('INFO', `任务已停止（CLI 退出码 ${code}）`)
      // 强杀路径（无 stopped 事件）：最终 Excel 可能没来得及写，
      // 但每个协会完成时已增量落盘 partial JSONL，提示用户可追溯
      if (state.currentOutputPath) {
        appendLog('INFO', `已完成的协会数据已增量保存：${state.currentOutputPath}.partial.jsonl（JSONL 格式，每行一个协会）`)
      }
    }
    return
  }
  if (code !== 0 && code !== 2) {
    appendLog('ERROR', `CLI 进程退出（代码 ${code}）`)
    $('progress-status').textContent = '异常退出'
    $('progress-status').className = 'log-status error'
  }
}

// ============== 进度项 UI ==============

function createProgressItem(name, index, total) {
  const div = document.createElement('div')
  div.className = 'progress-item'
  div.innerHTML = `
    <div class="progress-index">${index}/${total}</div>
    <div class="progress-icon running">●</div>
    <div class="progress-body">
      <div class="progress-name">${escapeHtml(name)}</div>
      <div class="progress-steps"></div>
    </div>
  `
  return div
}

function updateProgressItem(item) {
  const icon = item.element.querySelector('.progress-icon')
  icon.className = `progress-icon ${item.status}`
  icon.textContent = item.status === 'success' ? '✓' : item.status === 'failed' ? '✗' : item.status === 'stopped' ? '■' : '●'

  const stepsEl = item.element.querySelector('.progress-steps')
  const lastStep = item.steps[item.steps.length - 1]
  if (lastStep && !stepsEl.querySelector(`[data-step="${lastStep.step}-${item.steps.length}"]`)) {
    const stepDiv = document.createElement('div')
    stepDiv.className = 'progress-step'
    stepDiv.dataset.step = `${lastStep.step}-${item.steps.length}`
    const statusIcon = lastStep.status === 'success' ? '✅' : lastStep.status === 'failed' ? '❌' : '🔄'
    stepDiv.textContent = `${statusIcon} ${lastStep.message}`
    stepsEl.appendChild(stepDiv)
  }
}

// ============== 右侧进度详情面板 ==============

function appendDetail(association, message, status = 'running', isCost = false) {
  const list = $('detail-list')
  if (!list) return
  const time = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  const div = document.createElement('div')
  div.className = `detail-item ${status === 'success' ? 'success' : status === 'failed' ? 'error' : ''}`
  if (isCost) div.classList.add('cost-item')
  div.innerHTML = `
    <div class="detail-item-time">[${time}] ${escapeHtml(association)}</div>
    <div class="detail-item-content${isCost ? ' detail-item-cost' : ''}">${escapeHtml(message)}</div>
  `
  list.appendChild(div)
  list.scrollTop = list.scrollHeight
}

function updateCurrentTask(name, step) {
  const task = $('current-task')
  if (!task) return
  task.classList.remove('hidden')
  task.querySelector('.current-task-name').textContent = name
  task.querySelector('.current-task-step').textContent = step
}

// ============== 日志（弹窗内） ==============

function appendLog(level, message, association = '') {
  const stream = $('log-stream')
  if (!stream) {
    console.error('[appendLog] log-stream element not found!')
    return
  }
  const time = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  state.guiLog.push({ time, level, message: (association ? `[${association}] ` : '') + message })
  const line = document.createElement('div')
  line.className = 'log-line'
  const prefix = association ? `[${association}] ` : ''
  line.innerHTML = `<span class="log-time">[${time}]</span> <span class="log-level-${level.toLowerCase()}">[${level}]</span> ${escapeHtml(prefix + message)}`
  stream.appendChild(line)
  // 自动滚动到最新日志
  stream.scrollTop = stream.scrollHeight
}

// ============== 工具 ==============

function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

// ============== 事件绑定 ==============

function bindEvents() {
  $('btn-activate').addEventListener('click', handleActivate)
  $('btn-start').addEventListener('click', handleStart)
  $('btn-stop').addEventListener('click', handleStop)
  $('btn-refresh-credits').addEventListener('click', refreshCredits)
  $('btn-export-diag').addEventListener('click', handleExportDiag)
  $('btn-select-input').addEventListener('click', handleSelectInput)
  $('btn-open-folder').addEventListener('click', () => {
    if (state.lastOutput) client.system.openFolder(state.lastOutput)
  })
  $('btn-new-task').addEventListener('click', () => {
    $('progress-section').classList.add('hidden')
    $('result-section').classList.add('hidden')
    $('result-output').classList.add('hidden')
    $('associations-input').value = ''
    $('input-file-name').textContent = ''
    state.inputFilePath = ''
    $('detail-list').innerHTML = ''
    $('current-task').classList.add('hidden')
    $('log-stream').innerHTML = ''
    $('progress-status').textContent = '空闲'
    $('progress-status').className = 'log-status'
  })
  // 日志弹窗
  $('btn-show-log').addEventListener('click', () => $('log-modal').classList.remove('hidden'))
  $('btn-close-log-modal').addEventListener('click', () => $('log-modal').classList.add('hidden'))
  // 积分余额点击 → 弹出明细
  $('credits-balance').addEventListener('click', showCreditsDetail)
  $('btn-close-modal').addEventListener('click', () => $('credits-modal').classList.add('hidden'))
  // 弹窗遮罩点击关闭
  document.querySelectorAll('.modal-overlay').forEach((el) => {
    el.addEventListener('click', () => el.parentElement.classList.add('hidden'))
  })
}

async function handleExportDiag() {
  // 把 GUI 内存日志 + 本地文件聚合成 zip（主进程脱敏 token、裁剪大小）
  const guiLogText = (state.guiLog || [])
    .map((e) => `[${e.time}] [${e.level}] ${e.message}`)
    .join('\n')
  const today = new Date().toISOString().slice(0, 10)
  let res
  try {
    res = await client.system.exportDiagnostics(`协会客户端诊断包_${today}.zip`, guiLogText)
  } catch (e) {
    alert('导出失败：' + (e && e.message ? e.message : e))
    return
  }
  if (res === null || res === undefined) return  // 用户取消
  if (res && typeof res === 'object' && res.error) {
    alert('导出失败：' + res.error)
    return
  }
  alert('诊断包已导出：\n' + res + '\n\n可把这个 zip 文件发给客服排查。')
  client.system.openFolder(res)
}

async function handleSelectInput() {
  const result = await client.system.selectInputFile()
  if (result) {
    state.inputFilePath = result
    // 显示文件名
    const parts = result.replace(/\\/g, '/').split('/')
    $('input-file-name').textContent = `📎 ${parts[parts.length - 1]}`
  }
}

// ============== 积分明细弹窗 ==============

async function showCreditsDetail() {
  $('credits-modal').classList.remove('hidden')
  $('credits-detail-loading').classList.remove('hidden')
  $('credits-detail-table').classList.add('hidden')
  $('credits-detail-tbody').innerHTML = ''

  try {
    const result = await client.cli.getCreditsDetail(state.config.serverUrl, state.config.accessToken)
    if (!result.ok) throw new Error(result.error || '查询失败')
    const items = result.data?.items || []
    if (items.length === 0) {
      $('credits-detail-loading').textContent = '暂无消耗记录'
      return
    }
    const tbody = $('credits-detail-tbody')
    for (const item of items) {
      const tr = document.createElement('tr')
      const time = new Date(item.time).toLocaleString('zh-CN', { hour12: false })
      const summary = [item.association, item.stage].filter(Boolean).join(' · ')
      tr.innerHTML = `
        <td class="time">${escapeHtml(time)}</td>
        <td class="cost">${parseFloat(item.credit_cost || 0).toFixed(2)}</td>
        <td class="summary">${escapeHtml(summary || '-')}</td>
      `
      tbody.appendChild(tr)
    }
    $('credits-detail-loading').classList.add('hidden')
    $('credits-detail-table').classList.remove('hidden')
  } catch (err) {
    $('credits-detail-loading').textContent = `查询失败: ${err.message || err}`
  }
}

// 启动
console.log('[app.js] starting init, client available:', !!client)
if (!client) {
  document.body.innerHTML = '<div style="padding:40px;color:red;font-size:16px">客户端初始化失败：preload 未加载。请重启应用。</div>'
} else {
  init().catch((err) => console.error('[init failed]', err))
}
