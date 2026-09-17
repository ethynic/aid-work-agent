/** Name-scoped live desktop primitives. No account identifier or binding lookup.
 * The caller owns the desktop lock and task policy. This module never retries a
 * submitted send; durable request markers survive process restarts.
 */
import { createHash, randomUUID } from 'node:crypto'
import { mkdir, readFile, writeFile, unlink } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'
import { z } from 'zod'
import { CodedOperationError } from '../operations/types.js'
import { ResidentOcr, type OcrBox } from './ocrResident.js'
import { uniqueTailPositions } from './aligner.js'
import { ocrTextMatches, ocrNameMatches, ocrLabelMatches } from './ocrTextMatch.js'
import { segmentTextBubbles, textFromBubbleRegions, type Bounds, type TextBubbleRegion } from './bubbleSegmentation.js'
import { protectText, runDpapiCommand } from '../security/dpapi.js'
import { runPowerShellDriver } from './powershell.js'

const bounds = z.object({ x0: z.number().nonnegative(), y0: z.number().nonnegative(), x1: z.number().positive(), y1: z.number().positive() }).refine(b => b.x1 > b.x0 && b.y1 > b.y0)
const objectSchema = z.object({ type: z.enum(['text', 'image', 'card', 'file', 'system', 'unknown']), sender: z.enum(['peer', 'self', 'system', 'unknown']), text: z.string().max(20_000), bounds, clipped: z.boolean(), evidence_hash: z.string().regex(/^[a-f0-9]{64}$/) })
// exact_name is a legacy canonical-target confirmation flag, not exact OCR equality.
export const nameWindowSchema = z.object({ unchanged: z.literal(false), title: z.string(), exact_name: z.literal(true), unique_match: z.literal(true), name_match_mode:z.literal('ocr_fuzzy_unique').optional(), evidence_ref: z.string().regex(/^dpapi:[a-zA-Z0-9_-]+$/), complete: z.boolean(), frame: z.string().regex(/^[a-f0-9]{64}$/), objects: z.array(objectSchema).max(256), input_empty: z.boolean() })
export type NameWindow = z.infer<typeof nameWindowSchema>
export type NameRead = NameWindow | { unchanged: true; frame: string }
export type SendDiagnosticStage = 'precheck' | 'type' | 'enter' | 'submitted'
export type NameDriver = (request: Record<string, unknown>, signal?: AbortSignal, diagnostic?: (stage:SendDiagnosticStage)=>Promise<void>) => Promise<Record<string, unknown>>

interface CaptureImage { width:number;height:number;png:string;title_region:Bounds;input_region:Bounds }
interface LocalCapture extends CaptureImage { frame:string;full_frame:string;title_frame:string;rgba:string;message_region:Bounds }
export const sendCaptureSchema=z.object({width:z.number().int().positive(),height:z.number().int().positive(),png:z.string(),title_region:bounds,input_region:bounds,handle:z.number().int().positive(),rect:z.tuple([z.number(),z.number(),z.number().positive(),z.number().positive()])})
export type SendCapture=z.infer<typeof sendCaptureSchema>
const localOcr=new ResidentOcr()
const knownNames=new Set<string>()
const resolvedNameLabels=new Map<string,string>()
/** A transient badge may request another capture, never explain message pixels. */
export function hasNewMessageOverlay(boxes:OcrBox[],viewport:Bounds):boolean{
  const width=viewport.x1-viewport.x0
  return boxes.some(b=>ocrLabelMatches('0条新消息',b.text)&&b.score>=0.9&&b.score<=1
    &&b.x0>=viewport.x0+width*0.78&&b.x1<=viewport.x1
    &&b.y0>=viewport.y0&&b.y1<=viewport.y0+width*0.08
    &&b.x1>b.x0&&b.x1-b.x0<width*0.16&&b.y1>b.y0&&b.y1-b.y0<width*0.04)
}
export async function readWithoutOverlay<T>(read:()=>Promise<T>,signal?:AbortSignal,wait:(signal?:AbortSignal)=>Promise<unknown>=s=>delay(750,undefined,{signal:s})):Promise<T>{
  for(let attempt=0;;attempt++){
    signal?.throwIfAborted()
    try{const result=await read();signal?.throwIfAborted();return result}catch(error){
      if(!(error instanceof CodedOperationError)||error.message!=='transient_overlay'||attempt>=2)throw error
      signal?.throwIfAborted()
      await wait(signal)
    }
  }
}

/** A partial first bubble is opaque history, never newly observed text. Require
 * a flat cut edge close to the viewport top and crossing text; other crossing OCR boxes use center-based bubble assignment. */
export function splitClippedTop(regions:TextBubbleRegion[],boxes:OcrBox[],rgba:Uint8Array,width:number,viewport:Bounds){
  const first=regions[0],limit=Math.max(4,Math.round(width*0.015))
  if(!first||first.bounds.y0-viewport.y0>limit)return {regions,boxes,clipped:undefined}
  const fill=first.sender==='self'?[157,242,159]:[238,238,240]
  let edgePixels=0,fillPixels=0
  for(let x=first.bounds.x0;x<first.bounds.x1;x++){
    const i=(first.bounds.y0*width+x)*4
    if(fill.every((v,k)=>Math.abs(rgba[i+k]!-v)<=2))fillPixels++
    // A cut through text includes near-black ink and antialiasing as well as fill.
    // Use the same fill/ink mixture model as text-bubble segmentation.
    const scale=Math.min(1,rgba[i+1]!/fill[1]!)
    if(fill.every((v,k)=>Math.abs(rgba[i+k]!-v*scale)<=18))edgePixels++
  }
  if(fillPixels===0 || edgePixels<(first.bounds.x1-first.bounds.x0)*0.85 || !boxes.some(b=>b.y0<first.bounds.y0&&b.y1>first.bounds.y0&&b.x0>first.bounds.x0&&b.x1<first.bounds.x1))return {regions,boxes,clipped:undefined}
  const clipped={...first.bounds,y0:viewport.y0}
  const kept=boxes.filter(b=>!(b.y0<clipped.y1&&b.y1>clipped.y0&&b.x0<clipped.x1&&b.x1>clipped.x0))
  return {regions:regions.slice(1),boxes:kept,clipped}
}
/** Timestamp recognition is restricted to a short centered standalone row. */
export function isNameTimestamp(box:OcrBox,viewport:Bounds,bubbles:ReadonlyArray<{bounds:Bounds}>=[]):boolean{
 const width=viewport.x1-viewport.x0,height=box.y1-box.y0
 if(![box.x0,box.y0,box.x1,box.y1,box.score,viewport.x0,viewport.y0,viewport.x1,viewport.y1].every(Number.isFinite)
   ||width<=0||viewport.y1<=viewport.y0||box.score<0.8||box.score>1||height<=0||height>width*0.04
   ||box.x1<=box.x0||box.x1-box.x0>=width*0.25||box.x0<viewport.x0||box.x1>viewport.x1||box.y0<viewport.y0||box.y1>viewport.y1
   ||Math.abs((box.x0+box.x1-viewport.x0-viewport.x1)/2)>=width*0.08||/[\r\n\u2028\u2029]/.test(box.text))return false
 // A timestamp inside or crossing a bubble remains governed by bubble coverage.
 if(bubbles.some(({bounds:b})=>box.x0<b.x1&&box.x1>b.x0&&box.y0<b.y1&&box.y1>b.y0))return false
 const match=/^(?:(?:(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日|星期[一二三四五六日天]|周[一二三四五六日天]|昨天|今天)[ \t]*)?(?:上午|下午|晚上|凌晨|中午)?[ \t]*(\d{1,2}):(\d{2})$/.exec(box.text.trim())
 if(!match||Number(match[4])>23||Number(match[5])>59)return false
 if(match[2]){
   const year=Number(match[1]??2000),month=Number(match[2]),day=Number(match[3])
   if(year<1||month<1||month>12||day<1)return false
   const leap=year%4===0&&(year%100!==0||year%400===0)
   if(day>[31,leap?29:28,31,30,31,30,31,31,30,31,30,31][month-1]!)return false
 }
 return true
}
async function localPrimitive(request:Record<string,unknown>,signal?:AbortSignal):Promise<Record<string,unknown>>{
  const dir=join(tmpdir(),'aid-name-ocr');await mkdir(dir,{recursive:true})
  const path=join(dir,randomUUID()+'.dpapi')
  await writeFile(path,await protectText(JSON.stringify(request),(command,input)=>runDpapiCommand(command,input,signal)),{flag:'wx',signal})
  try{return await runPowerShellDriver({script:fileURLToPath(new URL('../../../drivers/ps1/name-ocr.ps1',import.meta.url)),args:['-InputFile',path],signal})}
  finally{await unlink(path).catch(()=>{})}
}
async function withCapture<T,C extends CaptureImage=LocalCapture>(signal:AbortSignal|undefined,fn:(c:C,path:string)=>Promise<T>,action='capture'):Promise<T>{
  const raw=await localPrimitive({action},signal)
  const c=(action==='send_capture'?sendCaptureSchema.parse(raw):raw) as unknown as C
  if(!Number.isInteger(c.width)||!Number.isInteger(c.height)||c.width*c.height>16_000_000)throw new CodedOperationError('UI_CHANGED','截图尺寸非法')
  const path=join(tmpdir(),`aid-ocr-${randomUUID()}.png`)
  await writeFile(path,Buffer.from(c.png,'base64'),{flag:'wx'})
  try{return await fn(c,path)}finally{await unlink(path).catch(()=>{})}
}
const crop=(r:Bounds):[number,number,number,number]=>[r.x0,r.y0,r.x1,r.y1]
async function assertTitle(c:CaptureImage,path:string,name:string,signal?:AbortSignal){
  const title=await localOcr.recognize(path,crop(c.title_region),signal)
  const candidate=uniqueOcrName(title,name)
  if(!ocrNameMatches(resolvedNameLabels.get(name)??name,candidate.text))throw new CodedOperationError('TARGET_AMBIGUOUS','标题与首次选中联系人不一致')
}
export function uniqueOcrName(rows:OcrBox[],name:string):OcrBox{
  const candidates=rows.filter(b=>ocrNameMatches(name,b.text))
  if(candidates.length!==1||!Number.isFinite(candidates[0]!.score)||candidates[0]!.score<.8||candidates[0]!.score>1)throw new CodedOperationError('TARGET_AMBIGUOUS','本地OCR无法唯一确认联系人')
  return candidates[0]!
}
function isUiLabel(text:string,labels:string[]):boolean{
  const label=text.normalize('NFKC').replace(/\s*\(\d+\)\s*$/u,'').trim()
  return labels.filter(expected=>ocrLabelMatches(expected,label)).length===1
}
export function uniqueContactRow(rows:OcrBox[],width:number,height:number,name:string):OcrBox{
  const heading=rows.filter(b=>isUiLabel(b.text,['联系人','最常使用'])&&b.x0<width*0.6)
  if(heading.length!==1)throw new CodedOperationError('TARGET_AMBIGUOUS',`联系人结果范围不完整；heading_count=${heading.length}`)
  const top=heading[0]!.y1
  const next=rows.filter(b=>b.y0>top&&b.x0<width*0.6&&isUiLabel(b.text,['群聊','聊天记录','功能','公众号','搜索网络']))
  const end=Math.min(Infinity,...next.map(b=>b.y0))
  const hidden=rows.some(b=>b.y0>top&&b.y0<end&&b.x0<width*0.6&&(isUiLabel(b.text,['更多','展开','查看更多'])||/\.\.\.|…/.test(b.text)))
  if(!Number.isFinite(end)||end>=height-4||hidden)throw new CodedOperationError('TARGET_AMBIGUOUS',`联系人分区未闭合；section_closed=${Number.isFinite(end)}; hidden_contact_marker=${hidden}`)
  return uniqueOcrName(rows.filter(b=>b.y0>top&&b.y1<end&&b.x0<width*0.6),name)
}
/** Unreadable candidates stay opaque so the watermark decides whether they
 * are old history or unprocessed content. Invalid geometry still rejects input. */
export function objectsFromTextRegions(regions:TextBubbleRegion[],boxes:OcrBox[]):NameWindow['objects'] {
  const checked=textFromBubbleRegions(regions,boxes)
  if(checked.kind==='gap'&&['invalid_geometry','resource_limit'].includes(checked.reason))throw new CodedOperationError('UI_CHANGED','ocr_gap')
  if(boxes.some(b=>!Number.isFinite(b.score)||b.score<0||b.score>1))throw new CodedOperationError('UI_CHANGED','ocr_gap')
  return regions.map(region=>{
    const assigned=boxes.filter(b=>(b.x0+b.x1)/2>=region.bounds.x0&&(b.x0+b.x1)/2<region.bounds.x1&&(b.y0+b.y1)/2>=region.bounds.y0&&(b.y0+b.y1)/2<region.bounds.y1)
    const extracted=textFromBubbleRegions([region],assigned)
    const readable=extracted.kind==='candidates'&&assigned.every(b=>b.score>=0.75)
    const text=readable?extracted.messages[0]!.text:''
    return {type:readable?'text':'image',sender:region.sender,text,bounds:region.bounds,clipped:false,evidence_hash:createHash('sha256').update(JSON.stringify([region,text])).digest('hex')}
  })
}

async function withSendCapture<T>(signal:AbortSignal|undefined,fn:(c:SendCapture,path:string)=>Promise<T>,action:'send_capture'):Promise<T>{
  return withCapture<T,SendCapture>(signal,fn,action)
}
export interface NameSubmitDependencies {
  capture: typeof withSendCapture
  title: typeof assertTitle
  primitive: typeof localPrimitive
}
/** Caller holds the desktop lock across this entire capture/OCR/input sequence. */
export async function submitNameWithCapture(request:Record<string,unknown>,signal?:AbortSignal,diagnostic?:Parameters<NameDriver>[2],dependencies:NameSubmitDependencies={capture:withSendCapture,title:assertTitle,primitive:localPrimitive}):Promise<Record<string,unknown>>{
    const name=String(request.target_name??'')
    signal?.throwIfAborted()
    await diagnostic?.('precheck')
    const started=performance.now()
    const capture=await dependencies.capture(signal,async(c,path)=>{
      signal?.throwIfAborted()
      const captured=performance.now()
      await dependencies.title(c,path,name,signal)
      return {c,capture_ms:captured-started,title_ocr_ms:performance.now()-captured}
    },'send_capture')
    signal?.throwIfAborted()
    await diagnostic?.('type')
    signal?.throwIfAborted()
    const submitStarted=performance.now()
    const entered=await dependencies.primitive({...request,action:'submit',handle:capture.c.handle,rect:capture.c.rect,input_region:capture.c.input_region},signal)
    if(entered.done!==true)throw new CodedOperationError('EXECUTION_UNKNOWN','粘贴与回车动作未确认完成')
    // No payload, recipient, screenshot or request content enters diagnostics.
    const driverTimings=entered.timings_ms as Record<string,unknown>|undefined
    const driverMs=Object.fromEntries(['click','clipboard','input','total'].flatMap(key=>typeof driverTimings?.[key]==='number'&&Number.isFinite(driverTimings[key])&&driverTimings[key]>=0?[[key,driverTimings[key]]]:[]))
    process.stderr.write(JSON.stringify({event:'name_send_timing',capture_ms:Math.round(capture.capture_ms),title_ocr_ms:Math.round(capture.title_ocr_ms),submit_ms:Math.round(performance.now()-submitStarted),total_ms:Math.round(performance.now()-started),driver_ms:driverMs})+'\n')
    return{submitted:true}
}

/** Existing PrintWindow + resident RapidOCR; no cloud message transcription. */
export const defaultNameDriver: NameDriver = async(request,signal,diagnostic)=>{
  const name=String(request.target_name??'')
  if(request.open===true){
    resolvedNameLabels.delete(name)
    await localPrimitive({action:'search',target_name:name},signal)
    await withCapture(signal,async(c,path)=>{
      const rows=await localOcr.recognize(path,undefined,signal)
      const b=uniqueContactRow(rows,c.width,c.height,name)
      await localPrimitive({action:'select',x:(b.x0+b.x1)/2,y:(b.y0+b.y1)/2},signal)
      resolvedNameLabels.set(name,b.text)
    },'search_capture')
    knownNames.add(name)
  }
  if(!knownNames.has(name))throw new CodedOperationError('TARGET_NOT_FOUND','须先通过本地OCR唯一名称定位')
  if(request.action==='submit')return submitNameWithCapture(request,signal,diagnostic)
  return readWithoutOverlay(()=>withCapture(signal,async(c,path)=>{
    if(knownNames.has(name)&&request.previous_frame===c.frame)return{unchanged:true,frame:c.frame}
    await assertTitle(c,path,name,signal)
    const r=c.message_region
    const rgba=Buffer.from(c.rgba,'base64')
    const candidates=segmentTextBubbles({width:c.width,height:c.height,rgba},{messageRegion:r,peerLeft:Math.round(r.x0+c.width*0.073),selfRight:Math.round(c.width*0.92),edgeTolerance:Math.round(c.width*0.015),peerFill:[238,238,240],selfFill:[157,242,159],minWidth:Math.max(15,Math.round(c.width*0.02)),minHeight:Math.max(12,Math.round(c.height*0.015))})
    const rawBoxes=(await localOcr.recognize(path,crop(r),signal)).map(b=>({...b,x0:b.x0+r.x0,x1:b.x1+r.x0,y0:b.y0+r.y0,y1:b.y1+r.y0}))
    if(hasNewMessageOverlay(rawBoxes,r))throw new CodedOperationError('UI_CHANGED','transient_overlay')
    const {regions,boxes,clipped}=splitClippedTop(candidates,rawBoxes,rgba,c.width,r)
    const objects=objectsFromTextRegions(regions,boxes)
    const isTimestamp=(box:OcrBox)=>isNameTimestamp(box,r,regions)
    // Unassigned OCR remains opaque content; pixels do not authenticate messages.
    for(const b of boxes){
      if(regions.some(t=>(b.x0+b.x1)/2>=t.bounds.x0&&(b.x0+b.x1)/2<t.bounds.x1&&(b.y0+b.y1)/2>=t.bounds.y0&&(b.y0+b.y1)/2<t.bounds.y1))continue
      if(isTimestamp(b))continue
      const bytes:Buffer[]=[];for(let y=Math.floor(b.y0);y<Math.ceil(b.y1);y++)bytes.push(rgba.subarray((y*c.width+Math.floor(b.x0))*4,(y*c.width+Math.ceil(b.x1))*4))
      objects.push({type:'image',sender:'system',text:'',bounds:b,clipped:false,evidence_hash:createHash('sha256').update(Buffer.concat(bytes)).digest('hex')})
    }
    if(clipped)objects.push({type:'image',sender:'system',text:'',bounds:clipped,clipped:true,evidence_hash:createHash('sha256').update(JSON.stringify(clipped)).digest('hex')})
    objects.sort((a,b)=>a.bounds.y0-b.bounds.y0)
    const input=await localOcr.recognize(path,crop(c.input_region),signal)
    const evidenceId=randomUUID(),root=join(process.env.LOCALAPPDATA??tmpdir(),'aid-weixin','ocr-evidence');await mkdir(root,{recursive:true})
    await writeFile(join(root,evidenceId+'.dpapi'),await protectText(c.png,(command,input)=>runDpapiCommand(command,input,signal)),{flag:'wx',signal})
    signal?.throwIfAborted()
    knownNames.add(name);if(knownNames.size>32){const oldest=knownNames.values().next().value!;knownNames.delete(oldest);resolvedNameLabels.delete(oldest)}
    return{unchanged:false,title:name,exact_name:true,unique_match:true,name_match_mode:'ocr_fuzzy_unique',evidence_ref:'dpapi:'+evidenceId,complete:true,input_empty:input.length===0,frame:c.frame,objects}
  }),signal)
}

export async function readNameSession(targetName: string, opts: { previousFrame?: string; signal?: AbortSignal; driver?: NameDriver; open?: boolean } = {}): Promise<NameRead> {
  if (!targetName.trim() || targetName.length > 128) throw new CodedOperationError('INVALID_ARGUMENT', '目标名称非法')
  const raw = await (opts.driver ?? defaultNameDriver)({ action: 'read', target_name: targetName, previous_frame: opts.previousFrame, open: opts.open ?? false }, opts.signal)
  if (raw.unchanged === true) {
    if (!opts.previousFrame || raw.frame !== opts.previousFrame) throw new CodedOperationError('UI_CHANGED', '无变化结果缺少有效前帧')
    return { unchanged: true, frame: opts.previousFrame }
  }
  const parsed = nameWindowSchema.safeParse(raw)
  if (!parsed.success || !ocrNameMatches(targetName,parsed.data.title) || !parsed.data.complete ||
      parsed.data.objects.some((o, i) => o.type === 'unknown' || o.sender === 'unknown' || (o.type === 'text' && !o.text.trim()) ||
        (['image','card','file'].includes(o.type) && o.text !== '') || (o.clipped && i !== 0))) {
    throw new CodedOperationError('UI_CHANGED', '当前会话或顶层消息识别不确定')
  }
  const objects = parsed.data.objects
  if (objects.some((o, i) => i > 0 && o.bounds.y0 < objects[i - 1]!.bounds.y0)) throw new CodedOperationError('UI_CHANGED', '消息顺序不确定')
  return parsed.data
}

/** OCR text compares approximately; image/card pixels are tracked
 * as opaque objects. Embedded screenshot text is never a message body.
 */
function sameObject(a:NameWindow['objects'][number],b:NameWindow['objects'][number]):boolean{
  return a.type===b.type&&a.sender===b.sender&&a.clipped===b.clipped
    &&(a.type==='text'||a.type==='system'?ocrTextMatches(a.text,b.text):a.evidence_hash===b.evidence_hash)
}
export function newNameObjects(before: Pick<NameWindow,'objects'>, after: Pick<NameWindow,'objects'>): NameWindow['objects'] | null {
  const old = before.objects.filter(o => !o.clipped)
  const current = after.objects.filter(o => !o.clipped)
  if (!old.length) return before.objects.length ? null : current
  const positions = uniqueTailPositions(old.slice(-1), current, sameObject)
  return positions.length === 1 ? current.slice(positions[0]! + 1) : null
}

export async function sendNameSessionOnce(args: { targetName: string; text: string; requestId: string; baseline: NameWindow; deadlineMs: number }, opts: { stateDir: string; signal?: AbortSignal; driver?: NameDriver }): Promise<{ status: 'submitted' | 'unknown'; evidence_ref?: string }> {
  if (!/^[a-f0-9-]{36}$/.test(args.requestId) || !args.text.trim() || args.text.length > 500 || /[\x00-\x1f\x7f]/.test(args.text) || !Number.isSafeInteger(args.deadlineMs) || args.deadlineMs <= Date.now()) throw new CodedOperationError('INVALID_ARGUMENT', '单条发送参数非法或已过期')
  opts={...opts,signal:AbortSignal.any([...(opts.signal?[opts.signal]:[]),AbortSignal.timeout(Math.min(2_147_483_647,Math.max(1,args.deadlineMs-Date.now())))])}
  const identity = createHash('sha256').update(JSON.stringify([args.targetName, args.text, args.baseline.frame])).digest('hex')
  await mkdir(opts.stateDir, { recursive: true })
  const marker = join(opts.stateDir, `${args.requestId}.json`)
  const { open } = await import('node:fs/promises')
  const journalStarted=performance.now()
  let stage:SendDiagnosticStage='precheck'
  const record=async(next:SendDiagnosticStage,errorCode?:string)=>{
    stage=next
    // Separate append-only diagnostic journal. No names, payload, screenshots,
    // driver request or exception text enter this file.
    const log=await open(marker+'.stages.jsonl','a')
    try{await log.writeFile(JSON.stringify({stage,at:new Date().toISOString(),elapsed_ms:Math.round(performance.now()-journalStarted),...(errorCode?{error_code:errorCode}:{})})+'\n');await log.sync()}finally{await log.close()}
  }
  const safeError=(error:unknown)=>error instanceof CodedOperationError ? (['pixel_gap','ocr_gap','frame_changed','transient_overlay'].includes(error.message)?'UI_CHANGED_'+error.message.toUpperCase():error.code) : 'INTERNAL_ERROR'
  try {
    const prior = JSON.parse(await readFile(marker, 'utf8')) as { digest: string }
    if (prior.digest !== identity) throw new CodedOperationError('BLOCKED', '重复请求内容不同')
    return { status: 'unknown' }
  } catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e }
  const driver = opts.driver ?? defaultNameDriver
  await record('precheck')
  // The live driver checks the current title and replaces the composer text. Message/sidebar
  // changes no longer prevent sending under the user's revised scope.
  // Exclusive creation is the irreversible local boundary. A crash leaves the
  // marker intact. Payload/recipient are not persisted in this public marker.
  const fd = await open(marker, 'wx')
  try { await fd.writeFile(JSON.stringify({ digest: identity, status: 'may_have_started' })); await fd.sync() } finally { await fd.close() }
  try {
    if (opts.signal?.aborted) {await record(stage,'CANCELLED');return { status: 'unknown' }}
    const acknowledged=await driver({ action: 'submit', target_name: args.targetName, text: args.text, expected_frame: args.baseline.frame, deadline_ms: args.deadlineMs }, opts.signal,record)
    opts.signal?.throwIfAborted()
    if(acknowledged.submitted!==true || Date.now()>=args.deadlineMs)throw new CodedOperationError('EXECUTION_UNKNOWN','发送操作未确认完成')
    const evidenceRef=`weixin-submission:${args.requestId}:1`
    await record('submitted')
    const receipt = await open(marker, 'w')
    try { await receipt.writeFile(JSON.stringify({ digest: identity, status: 'submitted', evidence_ref: evidenceRef })); await receipt.sync() } finally { await receipt.close() }
    return { status: 'submitted', evidence_ref: evidenceRef }
  } catch(error) {try{await record(stage,safeError(error))}catch{/* Original request marker still prevents retries. */}return { status: 'unknown' } }
}
