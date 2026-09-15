/** Offline synthetic pixels and OCR boxes: this is not a live ten-round conversation. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { isNameTimestamp, newNameObjects, splitClippedTop, type NameWindow } from '../src/platform/nameSession.js'
import type { Bounds, TextBubbleRegion } from '../src/platform/bubbleSegmentation.js'

type PixelFrame = {width:number;height:number;rgba:Uint8Array;region:Bounds;textRegions:Array<TextBubbleRegion & {text?:string}>;timeRegions:Bounds[]}
type Entry = { text:string; sender:'self'|'peer'; y:number; height:number }
const viewport={x0:20,y0:10,x1:470,y1:390}
function paint(frame:PixelFrame,b:Bounds,color:number[]){
 for(let y=Math.max(viewport.y0,b.y0);y<Math.min(viewport.y1,b.y1);y++)for(let x=b.x0;x<b.x1;x++){
  const i=(y*frame.width+x)*4;for(let k=0;k<3;k++)frame.rgba[i+k]=color[k]!
 }
}
function render(entries:Entry[],round:number){
 const frame:PixelFrame={width:480,height:400,rgba:new Uint8Array(480*400*4).fill(240),region:{...viewport},textRegions:[],timeRegions:[]}
 const bottom=entries.at(-1)!.y+entries.at(-1)!.height
 const shift=Math.max(0,bottom-365)
 const boxes:Array<{text:string;score:number;x0:number;x1:number;y0:number;y1:number}>=[]
 for(const e of entries){
  const y=e.y-shift;if(y+e.height<=viewport.y0)continue
  const b={x0:e.sender==='peer'?60:260,x1:e.sender==='peer'?200:430,y0:y,y1:y+e.height}
  const fill=e.sender==='peer'?[238,238,240]:[157,242,159]
  // Rounded corners and a one-pixel neutral shadow, not solid rectangular placeholders.
  paint(frame,{...b,x0:b.x0+2,x1:b.x1-2,y0:b.y0+1,y1:b.y1+1},[230,230,230])
  paint(frame,{...b,x0:b.x0+2,x1:b.x1-2},fill)
  paint(frame,{...b,y0:b.y0+2,y1:b.y1-2},fill)
  for(let line=0;line<e.height-12;line+=18)paint(frame,{x0:b.x0+12,x1:b.x1-12,y0:y+7+line,y1:y+9+line},[70,70,70])
  paint(frame,{x0:e.sender==='peer'?23:444,x1:e.sender==='peer'?38:459,y0:y,y1:y+15},[120,130,140])
  frame.textRegions.push({sender:e.sender,text:e.text,bounds:{...b,y0:Math.max(viewport.y0,y)}})
  boxes.push({text:e.text,score:.99,x0:b.x0+10,x1:b.x1-10,y0:y+3,y1:y+e.height-3})
 }
 const split=splitClippedTop(frame.textRegions,boxes,frame.rgba,frame.width,viewport)
 frame.textRegions=split.regions
 // A centered weekday row scrolls with the content; OCR right-edge drift does not change its pixels.
 const ty=290-shift
 if(round>=3&&ty>=viewport.y0&&ty+12<viewport.y1){
  const t={text:'星期一12:30',score:.99,x0:215,x1:255+(round%2),y0:ty,y1:ty+12}
  assert.equal(isNameTimestamp(t,viewport,frame.textRegions),true)
  frame.timeRegions.push(t);paint(frame,{...t,x1:253},[110,110,110])
 }
 paint(frame,{x0:20,x1:470,y0:10,y1:11},[210,210,210])
 paint(frame,{x0:469,x1:470,y0:10,y1:390},[210,210,210])
 const objects:NameWindow['objects']=frame.textRegions.map(b=>({type:'text',sender:b.sender,text:b.text!,bounds:b.bounds,clipped:false,evidence_hash:'c'.repeat(64)}))
 if(split.clipped)objects.unshift({type:'image',sender:'system',text:'',bounds:split.clipped,clipped:true,evidence_hash:'d'.repeat(64)})
 const window:NameWindow={unchanged:false,title:'synthetic',exact_name:true,unique_match:true,complete:true,input_empty:true,evidence_ref:'dpapi:synthetic',frame:round.toString(16).padStart(64,'0'),objects}
 return {frame,window,clipped:!!split.clipped}
}
function sequence(){
 const entries:Entry[]=[{text:'anchor',sender:'peer',y:35,height:38}]
 const frames=[render(entries,0)],added:string[][]=[]
 for(let round=1;round<=10;round++){
  const texts:string[]=[]
  for(const sender of (round===6?['self','peer']: [round%2?'self':'peer']) as Array<'self'|'peer'>){
   const last=entries.at(-1)!,text=`unique-${round}-${sender}`
   entries.push({text,sender,y:last.y+last.height+32,height:round%3===0?56:38});texts.push(text)
  }
  added.push(texts);frames.push(render(entries,round))
 }
 return {frames,added}
}
test('eleven synthetic frames preserve ten appends, clipped history, timestamp and simultaneous peer',()=>{
 const {frames,added}=sequence()
 assert.equal(frames.length,11)
 assert.ok(frames.some(f=>f.clipped),'fixture must actually exercise top clipping')
 assert.ok(frames.some(f=>f.frame.timeRegions.length),'fixture must include timestamp pixels')
 for(let i=1;i<frames.length;i++){
  assert.deepEqual(newNameObjects(frames[i-1]!.window,frames[i]!.window)?.map(o=>o.text),added[i-1],`objects transition ${i}`)
 }
})
