import test from 'node:test'
import assert from 'node:assert/strict'
import { readNameSession, newNameObjects, splitClippedTop, isNameTimestamp, type NameWindow } from '../src/platform/nameSession.js'

const object = (text: string, sender: 'peer' | 'self' = 'peer', y = 20): NameWindow['objects'][number] => ({ type:'text',sender,text,bounds:{x0:10,y0:y,x1:90,y1:y+15},clipped:false,evidence_hash:'c'.repeat(64) })
const baseline: NameWindow = { unchanged:false,title:'test-contact',exact_name:true,unique_match:true,complete:true,input_empty:true,evidence_ref:'dpapi:test_evidence',frame:'a'.repeat(64),objects:[object('old')] }

test('read要求精确标题、唯一名称、完整顶层对象；媒体正文不得接纳', async () => {
  for (const change of [{title:'other'}, {unique_match:false}, {complete:false}, {objects:[{...object('fake'),type:'image'}]}]) {
    await assert.rejects(readNameSession('test-contact',{driver:async()=>({...baseline,...change})}))
  }
  assert.deepEqual(await readNameSession('test-contact',{previousFrame:baseline.frame,driver:async()=>({unchanged:true,frame:baseline.frame})}),{unchanged:true,frame:baseline.frame})
})

test('媒体是结构对象，不递归文字；同文重叠歧义拒绝', () => {
  const media={...object(''),type:'image' as const}
  const before={...baseline,objects:[media,object('unique','peer',50)]}
  const after={...baseline,objects:[...before.objects,object('new','self',80)]}
  assert.equal(newNameObjects(before,after)?.length,1)
  assert.equal(newNameObjects({...baseline,objects:[object('x'),object('x','peer',50)]},{...baseline,objects:[object('x'),object('x','peer',50),object('x','peer',80)]}),null)
  assert.equal(newNameObjects({...baseline,objects:[{...object('partial'),clipped:true}]},{...baseline,objects:[object('new')]}),null)
})

test('only flat clipped top with crossing OCR is opaque; interior crossing stays a gap',()=>{
 const width=200,rgba=new Uint8Array(width*100*4),viewport={x0:0,y0:10,x1:200,y1:100}
 const region={sender:'self' as const,bounds:{x0:50,y0:12,x1:150,y1:40}}
 for(let x=50;x<150;x++)rgba.set([157,242,159,255],(12*width+x)*4)
 const boxes=[{text:'partial',score:1,x0:60,y0:11,x1:140,y1:22}]
 const clipped=splitClippedTop([region],boxes,rgba,width,viewport)
 assert.ok(clipped.clipped);assert.equal(clipped.regions.length,0);assert.equal(clipped.boxes.length,0)
 assert.equal(splitClippedTop([region],boxes,rgba,width,{...viewport,y0:0}).clipped,undefined)
 rgba.fill(240);assert.equal(splitClippedTop([region],boxes,rgba,width,viewport).clipped,undefined)
})

test('centered weekday timestamps remain metadata across OCR bounds drift',()=>{
 const viewport={x0:613,y0:150,x1:1914,y1:1570}
 const a={text:'星期一12:30',score:.99,x0:1188,y0:487,x1:1331,y1:514}
 assert.equal(isNameTimestamp(a,viewport),true)
 assert.equal(isNameTimestamp({...a,x1:1332,y0:293,y1:320},viewport),true)
 assert.equal(isNameTimestamp({...a,x0:744,x1:887},viewport),false)
 assert.equal(isNameTimestamp({...a,text:'正文星期一12:30'},viewport),false)
 assert.equal(isNameTimestamp({...a,score:.5},viewport),false)
})

test('timestamp exemption rejects invalid time/date, multiline and bubble intersections',()=>{
 const viewport={x0:613,y0:150,x1:1914,y1:1570}
 const box={text:'星期一12:30',score:.99,x0:1188,y0:487,x1:1331,y1:514}
 for(const text of ['99:99','24:00','12:60','0月1日12:30','13月1日12:30','4月31日12:30','2025年2月29日12:30','0000年1月1日12:30','星期一\n12:30','12:30\r','12:30\u2028'])assert.equal(isNameTimestamp({...box,text},viewport),false)
 for(const text of ['23:59','2024年2月29日12:30','星期一12:30'])assert.equal(isNameTimestamp({...box,text},viewport),true)
 assert.equal(isNameTimestamp({...box,y1:600},viewport),false)
 assert.equal(isNameTimestamp({...box,y0:140,y1:167},viewport),false)
 assert.equal(isNameTimestamp({...box,x0:NaN},viewport),false)
 assert.equal(isNameTimestamp(box,viewport,[{bounds:{x0:1100,y0:480,x1:1400,y1:520}}]),false)
 assert.equal(isNameTimestamp(box,viewport,[{bounds:{x0:1300,y0:500,x1:1400,y1:530}}]),false)
 assert.equal(isNameTimestamp(box,viewport,[{bounds:{x0:1400,y0:480,x1:1500,y1:520}}]),true)
})

test('顶部截断文字行允许40%墨迹抗锯齿，背景圆角及非截断候选仍拒绝',()=>{
 const width=200,viewport={x0:0,y0:10,x1:200,y1:100}
 const region={sender:'self' as const,bounds:{x0:50,y0:12,x1:150,y1:40}}
 const crossing={text:'synthetic',score:.99,x0:60,y0:11,x1:140,y1:22}
 const rgba=new Uint8Array(width*100*4).fill(240)
 const paint=(x:number,rgb:number[])=>rgba.set([...rgb,255],(12*width+x)*4)
 for(let x=50;x<150;x++)paint(x,[157,242,159])
 for(let x=70;x<110;x++)paint(x,x%2?[0,0,0]:[47,73,48])
 const result=splitClippedTop([region],[crossing],rgba,width,viewport)
 assert.deepEqual(result.clipped,{...region.bounds,y0:10});assert.equal(result.regions.length,0);assert.equal(result.boxes.length,0)
 assert.equal(splitClippedTop([region],[crossing],rgba,width,{...viewport,y0:0}).clipped,undefined)
 assert.equal(splitClippedTop([region],[{...crossing,y0:13}],rgba,width,viewport).clipped,undefined)
 for(let x=50;x<70;x++)paint(x,[240,240,240])
 assert.equal(splitClippedTop([region],[crossing],rgba,width,viewport).clipped,undefined)
 rgba.fill(240)
 assert.equal(splitClippedTop([region],[crossing],rgba,width,viewport).clipped,undefined)
 rgba.fill(0)
 assert.equal(splitClippedTop([region],[crossing],rgba,width,viewport).clipped,undefined)
})
