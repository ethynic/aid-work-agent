/**
 * 查本机端口表，供同机判定的规则 5、6 使用（设计 4.2）。
 *
 * Windows 下执行 netstat -ano -p udp 和 netstat -ano -p tcp 各一次，只看「本机地址」一列的端口号，
 * 实测约 30 毫秒。对端和 Runtime 在同一台电脑上时，它连入用的端口就在这张表里。
 *
 * 这里的输出按 Windows 版 netstat 的格式解析。命令失败或超时返回「查不到」（空集合加失败标记），
 * 调用方必须把 ok 为 false 的结果当作「端口表不可用」，不能当成「本机没有这些端口」：
 * 半张表会把本机的端口误判成不属于本机，进而放行采集。
 */
import { execFile } from 'node:child_process'
import type { PortProtocol } from './protocol.js'

/** netstat 平时几十毫秒；给到秒级是容许偶尔变慢，又不能拖过 15 秒的验证时限 */
export const NETSTAT_TIMEOUT_MS = 3_000
// netstat 的默认输出缓冲是 1 MB，套接字很多的机器上会超出并被当成失败
const NETSTAT_MAX_BUFFER_BYTES = 8 * 1024 * 1024

export interface LocalPorts {
  /** false 表示没查到（命令失败或超时），此时 ports 一定为空 */
  ok: boolean
  ports: Set<number>
}

export interface ExecOptions {
  windowsHide: true
  timeout: number
  maxBuffer: number
}

/** 执行外部命令的最小接口，与 child_process.execFile 的回调形式一致，测试可以注入 */
export type ExecLike = (file: string, args: readonly string[], options: ExecOptions, callback: (error: Error | null, stdout: string) => void) => unknown

const execFileAsync: ExecLike = (file, args, options, callback) => {
  execFile(file, [...args], options, (error, stdout) => callback(error, stdout))
}

/** 从 netstat 的输出里取出某个协议下本机地址一列的端口 */
export function parseNetstatPorts(stdout: string | undefined, protocol: PortProtocol): Set<number> {
  const ports = new Set<number>()
  for (const line of String(stdout || '').split('\n')) {
    // 每行是「协议 本机地址 对端地址 [状态] 进程」，本机地址形如 192.168.1.2:5000 或 [::]:5000
    const [name, local] = line.trim().split(/\s+/)
    if (!name || !local || name.toUpperCase() !== protocol.toUpperCase()) continue
    const port = local.slice(local.lastIndexOf(':') + 1)
    if (/^\d+$/.test(port)) ports.add(Number(port))
  }
  return ports
}

export function readLocalPorts(protocol: PortProtocol, exec: ExecLike = execFileAsync): Promise<LocalPorts> {
  return new Promise((resolve) => {
    exec('netstat', ['-ano', '-p', protocol], { windowsHide: true, timeout: NETSTAT_TIMEOUT_MS, maxBuffer: NETSTAT_MAX_BUFFER_BYTES }, (error, stdout) => {
      if (error) {
        resolve({ ok: false, ports: new Set() })
        return
      }
      resolve({ ok: true, ports: parseNetstatPorts(stdout, protocol) })
    })
  })
}
