import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { getDefaultEnvironment, StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

export const NATIVE_VERSION = '0.43.0'
export const NATIVE_SHA256 = 'ebcef5ebea93c7ccf0fe3f8c18eee908ae9174dfef8e52a3dda6f24a59f630dc'

export function nativePath(root) {
  return join(root, 'node_modules', '@agent-browser', 'cli-win32-x64-msvc', 'bin', 'agent-browser.exe')
}

export function chromePath() {
  const windows = process.env.SystemRoot ?? process.env.WINDIR
  const drive = windows && dirname(windows)
  const directories = [
    process.env.ProgramFiles ?? (drive && join(drive, 'Program Files')),
    process.env['ProgramFiles(x86)'] ?? (drive && join(drive, 'Program Files (x86)')),
    process.env.LOCALAPPDATA,
  ]
  return directories.filter(Boolean).map(directory => join(directory, 'Google', 'Chrome', 'Application', 'chrome.exe')).find(existsSync)
}

export async function openNative(root, profiles) {
  const base = resolve(tmpdir())
  const home = mkdtempSync(join(base, 'aid-browser-'))
  const config = join(home, 'config.json')
  const client = new Client({ name: 'aid-browser-provider', version: '1' })
  writeFileSync(config, '{}\n')
  const env = {
    ...getDefaultEnvironment(),
    AGENT_BROWSER_SOCKET_DIR: home,
    AGENT_BROWSER_FLOWS_DIR: join(home, 'flows'),
    AGENT_BROWSER_SCREENSHOT_DIR: join(home, 'screenshots'),
    AGENT_BROWSER_DOWNLOAD_PATH: join(home, 'downloads'),
    AGENT_BROWSER_HEADED: '1',
  }
  const chrome = chromePath()
  if (chrome) env.AGENT_BROWSER_EXECUTABLE_PATH = chrome
  const transport = new StdioClientTransport({
    command: nativePath(root), args: ['mcp', '--tools', profiles, '--config', config], env, stderr: 'ignore',
  })
  const dispose = () => {
    if (dirname(home) === base) rmSync(home, { recursive: true, force: true })
  }
  try {
    await client.connect(transport)
    if (client.getServerVersion()?.version !== NATIVE_VERSION) throw new Error('浏览器执行器版本与发行清单不一致')
  } catch (error) {
    await client.close().catch(() => {})
    dispose()
    throw error
  }
  return {
    client,
    async close({ closeBrowsers = false } = {}) {
      try {
        if (closeBrowsers) {
          await client.callTool({ name: 'agent_browser_close', arguments: { all: true } }, undefined, { timeout: 8000 })
        }
      } finally {
        await client.close()
        dispose()
      }
    },
  }
}

export async function listNativeTools(client) {
  const tools = []
  let cursor
  do {
    const result = await client.listTools(cursor ? { cursor } : {})
    tools.push(...result.tools)
    cursor = result.nextCursor
  } while (cursor)
  return tools
}
