import test from 'node:test'
import assert from 'node:assert/strict'
import { runDpapiCommand } from '../src/security/dpapi.js'

test('DPAPI runner terminates sleeping PowerShell on timeout', {skip:process.platform!=='win32'},async()=>{
 const started=performance.now()
 await assert.rejects(runDpapiCommand('Start-Sleep -Seconds 10','',AbortSignal.timeout(250)),{name:'AbortError'})
 assert.ok(performance.now()-started<3000)
})

test('DPAPI runner does not start for an already cancelled call',async()=>{
 const controller=new AbortController();controller.abort()
 await assert.rejects(runDpapiCommand('Start-Sleep -Seconds 10','',controller.signal),{name:'AbortError'})
})
