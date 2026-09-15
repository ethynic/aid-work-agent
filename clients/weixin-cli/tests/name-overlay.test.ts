import test from 'node:test'
import assert from 'node:assert/strict'
import { hasNewMessageOverlay, readWithoutOverlay } from '../src/platform/nameSession.js'
import { CodedOperationError } from '../src/operations/types.js'

const viewport={x0:613,y0:150,x1:1914,y1:1570}
const badge={text:'0条新消息',score:0.99,x0:1690,y0:193,x1:1835,y1:228}
test('真实浮层几何仅触发重采样；正文、位置和置信度分叉不识别',()=>{
  assert.equal(hasNewMessageOverlay([badge],viewport),true)
  for(const change of [{text:'10条新消息'},{text:'0条新消息图片'},{score:0.8},{x0:744,x1:889},{y0:400,y1:435},{x1:2000}])
    assert.equal(hasNewMessageOverlay([{...badge,...change}],viewport),false)
})
test('持续浮层最多三次采样，其他错误不重试',async()=>{
  for(const reason of ['transient_overlay','pixel_gap','ocr_gap']){
    let reads=0
    await assert.rejects(readWithoutOverlay(async()=>{reads++;throw new CodedOperationError('UI_CHANGED',reason)},undefined,async()=>{}))
    assert.equal(reads,reason==='transient_overlay'?3:1)
  }
})
test('等待中取消立即结束且不再次采样',async()=>{
  const controller=new AbortController();let reads=0
  const started=Date.now()
  const pending=readWithoutOverlay(async()=>{reads++;throw new CodedOperationError('UI_CHANGED','transient_overlay')},controller.signal)
  setTimeout(()=>controller.abort(),25)
  await assert.rejects(pending)
  assert.equal(reads,1);assert.ok(Date.now()-started<500)
})
test('读取完成前取消也拒绝返回结果',async()=>{
  const controller=new AbortController()
  await assert.rejects(readWithoutOverlay(async()=>{controller.abort();return 'stale'},controller.signal))
})
