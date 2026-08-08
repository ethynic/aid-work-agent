/**
 * CLI Runner —— spawn association-cli.exe 子进程，流式解析 stdout NDJSON 事件。
 *
 * 设计文档 §4.2。
 */

import { spawn, type ChildProcess } from 'node:child_process'
import { EventEmitter } from 'node:events'
import path from 'node:path'
import { StringDecoder } from 'node:string_decoder'

/** CLI stdout 输出的 NDJSON 事件（设计文档 §9.1） */
export interface CliEvent {
  event: 'start' | 'progress' | 'billing' | 'log' | 'error' | 'complete'
  [key: string]: unknown
}

export class CliRunner extends EventEmitter {
  private process: ChildProcess | null = null

  /**
   * @param cliPath CLI 可执行文件路径（打包模式 exe，开发模式 'python'）
   * @param argsPrefix 参数前缀（开发模式 ['../association-client-cli/main.py']，打包模式 []）
   */
  constructor(private cliPath: string, private argsPrefix: string[] = []) {
    super()
  }

  /**
   * 激活命令。
   * @returns CLI stdout 的 JSON（含 access_token 等）
   */
  async activate(code: string, serverUrl: string, clientName?: string): Promise<Record<string, unknown>> {
    return this.runSync([
      'activate', '--code', code, '--server-url', serverUrl,
      ...(clientName ? ['--client-name', clientName] : []),
    ])
  }

  /** 查询积分余额。 */
  async getCredits(serverUrl: string, accessToken: string): Promise<Record<string, unknown>> {
    return this.runSync([
      'credits', '--server-url', serverUrl,
    ], {
      ASSOCIATION_CLIENT_ACCESS_TOKEN: accessToken,
    })
  }

  /**
   * 协会收集命令（异步流式）。
   * 事件通过 'event' EventEmitter 推出；结束时 emit 'close'（含退出码）。
   */
  collect(
    associations: string[],
    outputPath: string,
    serverUrl: string,
    accessToken: string,
    inputPath?: string,
  ): void {
    const args = [
      ...this.argsPrefix,
      'collect',
      '--output', outputPath,
      '--server-url', serverUrl,
    ]
    // 有输入文件时用 --input，否则用 --associations
    if (inputPath) {
      args.push('--input', inputPath)
    } else {
      args.push('--associations', associations.join(','))
    }

    this.process = spawn(this.cliPath, args, {
      windowsHide: false,
      env: {
        ...process.env,
        ASSOCIATION_CLIENT_ACCESS_TOKEN: accessToken,
      },
    })

    // 用 StringDecoder 处理 UTF-8 多字节边界：中文 3 字节跨 chunk 时，
    // chunk.toString('utf-8') 会把不完整字节替换成 U+FFFD 导致乱码；
    // StringDecoder 缓存末尾不完整字节，下次 chunk 拼接后解码。
    const decoder = new StringDecoder('utf-8')
    let buffer = ''
    this.process.stdout?.on('data', (chunk: Buffer) => {
      buffer += decoder.write(chunk)
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.trim()) continue
        try {
          const evt: CliEvent = JSON.parse(line)
          this.emit('event', evt)
        } catch {
          // 非 JSON 行，作为 debug 日志转发
          this.emit('event', { event: 'log', level: 'DEBUG', message: line })
        }
      }
    })

    const stderrDecoder = new StringDecoder('utf-8')
    this.process.stderr?.on('data', (chunk: Buffer) => {
      const text = stderrDecoder.write(chunk).trim()
      if (text) {
        this.emit('event', { event: 'log', level: 'INFO', message: text })
      }
    })

    this.process.on('close', (code: number | null) => {
      this.emit('close', code ?? 0)
      this.process = null
    })

    this.process.on('error', (err: Error) => {
      this.emit('event', { event: 'error', error_code: 'CLI_SPAWN_FAILED', message: err.message })
      this.emit('close', 1)
      this.process = null
    })
  }

  /** 终止当前收集任务。 */
  kill(): void {
    if (this.process) {
      this.process.kill()
      this.process = null
    }
  }

  /** 同步运行命令（等进程退出，收集 stdout）——公开方法。 */
  runSyncCommand(args: string[], extraEnv?: Record<string, string>): Promise<Record<string, unknown>> {
    return this.runSync(args, extraEnv)
  }

  /** 同步运行命令（等进程退出，收集 stdout）。 */
  private runSync(args: string[], extraEnv?: Record<string, string>): Promise<Record<string, unknown>> {
    return new Promise((resolve, reject) => {
      const proc = spawn(this.cliPath, [...this.argsPrefix, ...args], {
        windowsHide: false,
        env: { ...process.env, ...extraEnv },
      })
      let stdout = ''
      let stderr = ''
      proc.stdout.on('data', (c: Buffer) => { stdout += c.toString('utf-8') })
      proc.stderr.on('data', (c: Buffer) => { stderr += c.toString('utf-8') })
      proc.on('close', (code: number | null) => {
        if (code === 0) {
          // 取 stdout 最后一行 JSON
          const lines = stdout.trim().split('\n')
          const lastLine = lines[lines.length - 1]
          try {
            resolve(JSON.parse(lastLine))
          } catch {
            reject(new Error(`CLI 输出解析失败: ${lastLine}`))
          }
        } else {
          reject(new Error(stderr.trim() || `CLI 退出码 ${code}`))
        }
      })
      proc.on('error', (err: Error) => reject(err))
    })
  }
}

/** 解析 CLI exe 路径（开发模式用 python，打包后用 extraResources 里的 exe）。 */
export function resolveCliPath(isDev: boolean): string {
  if (isDev) {
    // 开发模式：用 python 运行 CLI 工程的 main.py
    return 'python'
  }
  // 打包模式：extraResources/cli/association-cli.exe
  const cliPath = path.join(process.resourcesPath, 'cli', 'association-cli.exe')
  return cliPath
}

/** 开发模式下构造 collect 的额外参数（python main.py ...）。 */
export function devCliArgs(): string[] {
  const cliMain = path.resolve(__dirname, '..', '..', '..', 'association-client-cli', 'main.py')
  return [cliMain]
}
