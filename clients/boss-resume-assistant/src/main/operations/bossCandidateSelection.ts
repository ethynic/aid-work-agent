/** Separate tool names prevent older Providers from stripping an identity constraint and greeting by name. */
import { GreetExecutor } from '../boss/GreetExecutor.js'
import { candidateOption, candidateScrollEvent, loadedCandidates, resolveCandidateReference, hasLoadedRecommendationList } from '../boss/LoadedCandidateNavigation.js'
import { FILTER_SNAPSHOT_OPTIONS } from '../boss/FilterSetter.js'
import { inspectRecommendationResults } from '../boss/RecommendationResultBoundary.js'
import { locateResumeCanvas } from '../boss/ResumeReader.js'
import { defaultSessionFactory, runBossOperation, type BossSessionFactory } from './bossContext.js'
import { CodedOperationError, CancelledError, type BossOperation } from './types.js'
import { BusinessOperationState, withBusinessRecovery } from '../boss/BusinessOperationState.js'
import { runVerifiedPageOperation } from './pageOperation.js'

export interface BossLookupCandidatesArgs { names: string[] }
export interface BossGreetSelectedArgs { name: string; candidate_ref: string }
const recommend = (url: string): boolean => /^https:\/\/www\.zhipin\.com\/web\/chat\/recommend(?:[?#]|$)/.test(url)

export function createBossLookupCandidatesOperation(factory: BossSessionFactory = defaultSessionFactory): BossOperation<BossLookupCandidatesArgs> {
  return { name:'boss_lookup_candidates', execute(args,ctx) {
    return runBossOperation({kind:'readonly',name:'boss_lookup_candidates'},ctx,factory,()=>
      !Array.isArray(args?.names) || args.names.length<1 || args.names.length>3 || args.names.some(n=>typeof n!=='string'||!n.trim()||n.length>30)
        ? 'names必须是1-3个非空姓名（最多30字符）' : null,
      async session=>{
        const verified = await runVerifiedPageOperation({ name: 'boss_lookup_candidates', goal: '只读查询原姓名候选卡片',
          expected: '已加载推荐列表或明确空结果可读，逐名返回卡片或未找到', operationClass: 'readonly' }, async attempt => {
        if (!recommend(await session.getUrl())) throw new CodedOperationError('WRONG_PAGE','请在推荐牛人列表查询候选卡片')
        const snap=await session.snapshot(FILTER_SNAPSHOT_OPTIONS),state=inspectRecommendationResults(snap),cards=loadedCandidates(snap)
        attempt.verified({ status: hasLoadedRecommendationList(snap) || !!state.stopReason ? 'met' : 'inconclusive',
          evidence: ['读取已加载列表结构和结果边界；没有结构时不把遮挡误判为无人'] })
        if (ctx.signal.aborted) throw new CancelledError()
        const names=[...new Set(args.names.map(n=>n.trim()))]
        const groups=names.map(name=>({name,candidates:cards.filter(c=>c.name===name&&!c.excluded).map((c,i)=>candidateOption(c,i+1))}))
        return {message:'已只读查询候选卡片；请结合用户的选择使用对应candidate_ref，未发送招呼',effect:'none' as const,
          data:{candidates:groups,missing_names:groups.filter(g=>!g.candidates.length).map(g=>g.name),
            ...(state.stopReason ? {stop_reason:state.stopReason} : {})}}
        })
        return { ...verified.value, data: { ...verified.value.data, operation_recovery: verified.facts } }
      })
  }}
}

export function createBossGreetSelectedOperation(factory: BossSessionFactory = defaultSessionFactory): BossOperation<BossGreetSelectedArgs> {
  return {name:'boss_greet_selected', execute(args,ctx) {
    return runBossOperation({kind:'write',name:'boss_greet_selected'},ctx,factory,()=>{
      if (typeof args?.name!=='string'||!args.name.trim()||args.name.length>30) return 'name必须是非空姓名（最多30字符）'
      if (typeof args?.candidate_ref!=='string'||args.candidate_ref.length!==91||!/^boss-card-v1:\d{13}:[a-f0-9]{64}$/.test(args.candidate_ref)) return '必须提供候选卡片回执中的有效candidate_ref，不能只传姓名'
      return null
    },async(session,tracker)=>{
      const state = new BusinessOperationState()
      return withBusinessRecovery(state, { name: 'boss_greet_selected', goal: '向原候选引用的候选人打招呼',
        expected: '原候选引用身份对应的卡片翻转为继续沟通', operationClass: 'external_effect', target: args.name.trim() }, async () => {
      if (!recommend(await session.getUrl())) throw new CodedOperationError('WRONG_PAGE','请在推荐牛人列表使用候选引用打招呼')
      const name=args.name.trim(),probe=await session.snapshot(FILTER_SNAPSHOT_OPTIONS)
      if (locateResumeCanvas(probe)) throw new CodedOperationError('WRONG_PAGE','请先关闭简历详情，再在推荐牛人列表按候选引用打招呼')
      const selected=resolveCandidateReference(probe,name,args.candidate_ref)
      if (!selected) throw new CodedOperationError('UI_CHANGED','候选引用已过期或身份、账号、职位不匹配，未发送；请只读重新查询，不退回姓名发送')
      if (ctx.signal.aborted) throw new CancelledError()
      if (selected.buttonText==='继续沟通') return {message:'该候选人已经打过招呼，本次未重复发送',effect:'none',data:{greeted:0,already_greeted:true,greeted_names:[]}}
      const executor=new GreetExecutor({state,snapshot:()=>session.snapshot(FILTER_SNAPSHOT_OPTIONS),click:session.click,
        scroll:(delta,snap)=>{const e=candidateScrollEvent(snap,delta);return session.mouseWheel(e.x,e.y,e.deltaY)},
        signal:ctx.signal,onProgress:done=>{tracker.completed=done;ctx.progress({stage:'execute',current:done,total:1,message:'已向选定候选人打招呼'})}})
      const result=await executor.greetVisible({names:[name],limit:1,selection:{name,candidate_ref:args.candidate_ref}})
      return {message:result.greeted===1?'已向用户选定的候选人打招呼':'未向指定候选人发送招呼，请重新查询候选卡片',
        data:{greeted:result.greeted,greeted_names:result.greetedNames??[],missing_names:result.missingNames??[],
          ...(result.stopReason ? {stop_reason:result.stopReason} : {})}}
      })
    })
  }}
}
