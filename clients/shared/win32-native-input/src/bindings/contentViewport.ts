import koffi from 'koffi'
import type { ContentViewport, Rect } from '../types.js'

// UIAutomationClient.h: IUIAutomation / IUIAutomationElement / TreeWalker vtables.
// Only geometry/control type is read. Never traverse inside a web Document.
function comApi() {
  const ole32 = koffi.load('ole32.dll')
  return {
    initialize: ole32.func('int32 __stdcall CoInitializeEx(void *reserved, uint32 flags)'),
    uninitialize: ole32.func('void __stdcall CoUninitialize()'),
    create: ole32.func('int32 __stdcall CoCreateInstance(void *cls, void *outer, uint32 flags, void *iid, _Out_ void **object)'),
  }
}
const releaseType = koffi.proto('uint32 __stdcall WniUiaRelease(void *self)')
const fromHandleType = koffi.proto('int32 __stdcall WniUiaFromHandle(void *self, intptr hwnd, _Out_ void **element)')
const walkerType = koffi.proto('int32 __stdcall WniUiaWalker(void *self, _Out_ void **walker)')
const childType = koffi.proto('int32 __stdcall WniUiaChild(void *self, void *element, _Out_ void **found)')
const integerType = koffi.proto('int32 __stdcall WniUiaInteger(void *self, _Out_ int32 *value)')
const boundsType = koffi.proto('int32 __stdcall WniUiaBounds(void *self, void *rect)')
type ComPointer = NonNullable<Parameters<typeof koffi.decode>[0]>

function guid(value: string): Buffer {
  const [a, b, c, d, e] = value.split('-') as [string, string, string, string, string]
  const buffer = Buffer.alloc(16)
  buffer.writeUInt32LE(parseInt(a, 16)); buffer.writeUInt16LE(parseInt(b, 16), 4); buffer.writeUInt16LE(parseInt(c, 16), 6)
  Buffer.from(d + e, 'hex').copy(buffer, 8)
  return buffer
}

function invoke(object: ComPointer, slot: number, type: ReturnType<typeof koffi.proto>, ...args: unknown[]): number {
  const table = koffi.decode(object, 'void *')
  const address = koffi.decode(table, slot * koffi.sizeof('void *'), 'void *')
  return koffi.call(address, type, object, ...args) as number
}
function check(hr: number): void {
  if (hr < 0) throw new Error(`Cannot read browser content viewport (UIA HRESULT 0x${(hr >>> 0).toString(16)}); input refused`)
}
function release(object: ComPointer | null): void {
  if (object) invoke(object, 2, releaseType)
}
function integer(object: ComPointer, slot: number): number {
  const value = [0]
  check(invoke(object, slot, integerType, value))
  return value[0]!
}

/** A fresh top-level web viewport, independent of legacy RenderWidget HWNDs. */
export function openContentViewport(hwnd: number): ContentViewport {
  const { initialize, uninitialize, create } = comApi()
  const initialized = initialize(null, 0) as number // MTA; tolerate an existing different apartment.
  if (initialized < 0 && initialized !== -2147417850) check(initialized) // RPC_E_CHANGED_MODE
  const automation: (ComPointer | null)[] = [null]
  const root: (ComPointer | null)[] = [null]
  const walker: (ComPointer | null)[] = [null]
  const documents: ComPointer[] = []
  let closed = false
  const close = () => {
    if (closed) return
    closed = true
    for (const document of documents) release(document)
    release(walker[0] ?? null); release(root[0] ?? null); release(automation[0] ?? null)
    if (initialized >= 0) uninitialize()
  }
  try {
    check(create(guid('E22AD333-B25F-460C-83D0-0581107395C9'), null, 1,
      guid('30CBE57D-D9D0-452A-AB13-7AC5AC4825EE'), automation) as number)
    check(invoke(automation[0]!, 6, fromHandleType, hwnd, root))
    check(invoke(automation[0]!, 14, walkerType, walker))
    const visit = (element: ComPointer): void => {
      if (integer(element, 21) === 50030) { // UIA_DocumentControlTypeId
        if (!integer(element, 38)) {
          // Keep our own interface reference; traversal releases each child.
          const addRef = koffi.decode(koffi.decode(element, 'void *'), koffi.sizeof('void *'), 'void *')
          koffi.call(addRef, releaseType, element)
          documents.push(element)
        }
        return // Exclude iframe documents and all web page descendants.
      }
      const child: (ComPointer | null)[] = [null]
      check(invoke(walker[0]!, 4, childType, element, child))
      let current = child[0]
      while (current) {
        const next: (ComPointer | null)[] = [null]
        try {
          visit(current)
          check(invoke(walker[0]!, 6, childType, current, next))
        } finally { release(current) }
        current = next[0]
      }
    }
    visit(root[0]!)
    if (documents.length !== 1) throw new Error(`Expected exactly one visible browser content document; found ${documents.length}; input refused`)
    return {
      readRect(): Rect {
        if (closed || integer(documents[0]!, 38)) throw new Error('Browser content observation is no longer visible; input refused')
        // Client COM interface returns RECT (four LONGs), not provider UiaRect doubles.
        const rect = Buffer.alloc(16)
        check(invoke(documents[0]!, 43, boundsType, rect))
        return { left: rect.readInt32LE(0), top: rect.readInt32LE(4), right: rect.readInt32LE(8), bottom: rect.readInt32LE(12) }
      },
      close,
    }
  } catch (error) { close(); throw error }
}
