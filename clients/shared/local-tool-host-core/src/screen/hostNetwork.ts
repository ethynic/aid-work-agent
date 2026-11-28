/**
 * Runtime Host 自己能拿到的两样本机证据（设计 4.2）：本机全部网卡地址，以及用系统解析器
 * 解析观看端的 mDNS 名字。浏览器隐藏地址时只公布一个 mDNS 名字，明文地址直接用，是名字才解析。
 *
 * 名字解析只用于同机判定，不再是连上的前提（内网直连靠 Runtime 接受观看端的连接探测）。
 * 解析不出、超时都不是错误：结论只是「拿不到这条证据」。
 */
import { lookup } from 'node:dns'
import { networkInterfaces, type NetworkInterfaceInfo } from 'node:os'
import { normalizeAddress } from './candidate.js'
import { HOST_LOOKUP_TIMEOUT_MS } from './protocol.js'

export type HostLookupResult = { ok: true; address: string } | { ok: false; code: string }

/** 与 dns.lookup 的回调形式一致，测试可以注入 */
export type HostLookup = (hostname: string, options: { family: 4 }, callback: (error: NodeJS.ErrnoException | null, address: string) => void) => unknown

const systemLookup: HostLookup = (hostname, options, callback) => {
  lookup(hostname, options, callback)
}

/** 本机全部网卡地址（含回环和 IPv6），归一化、去重 */
export function localAddresses(interfaces: Readonly<Record<string, NetworkInterfaceInfo[] | null | undefined>> = networkInterfaces()): string[] {
  const addresses: string[] = []
  for (const nics of Object.values(interfaces)) {
    for (const nic of nics ?? []) {
      const address = normalizeAddress(nic.address)
      if (address) addresses.push(address)
    }
  }
  return [...new Set(addresses)]
}

/**
 * 只解析 .local 结尾的 mDNS 名字，用系统解析器，只要 IPv4 地址，2 秒超时。
 * 永不抛异常：失败时返回 {ok: false, code}。
 */
export function resolveHost(name: string, dnsLookup: HostLookup = systemLookup): Promise<HostLookupResult> {
  if (!/\.local$/i.test(name)) return Promise.resolve({ ok: false, code: 'NOT_MDNS' })
  return new Promise((resolve) => {
    // 定时器不阻止进程退出；解析回调先到时就把它清掉
    const timer = setTimeout(() => resolve({ ok: false, code: 'TIMEOUT' }), HOST_LOOKUP_TIMEOUT_MS)
    timer.unref()
    const done = (result: HostLookupResult): void => {
      clearTimeout(timer)
      resolve(result)
    }
    try {
      dnsLookup(name, { family: 4 }, (error, address) => {
        done(error ? { ok: false, code: error.code || 'ERROR' } : { ok: true, address })
      })
    } catch {
      done({ ok: false, code: 'ERROR' })
    }
  })
}
