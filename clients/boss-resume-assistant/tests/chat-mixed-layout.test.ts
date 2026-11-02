import assert from 'node:assert/strict'
import test from 'node:test'
import { ChatReadExecutor, conversationListNamesOf, headerContactOf } from '../src/main/boss/ChatReadExecutor.js'
import { ChatOpenExecutor } from '../src/main/boss/ChatOpenExecutor.js'
import { ChatSearchExecutor } from '../src/main/boss/ChatSearchExecutor.js'
import { buildChatSnapshot, conversationItem, yangSnapshot, type FixtureNode } from './chatReadFixture.js'

const ad = (): FixtureNode => ({ tag: 'DIV', cls: 'batch-chat-intention', bounds: [198,200,339,76], children: [
  { tag: '#text', text: '目标用户', bounds: [264,210,60,17] },
  { tag: 'SPAN', cls: 'badge-count', bounds: [238,210,20,16], children: [{tag:'#text',text:'9',bounds:[240,210,10,14]}] },
] })
const pinned = (): FixtureNode => {
 const row=conversationItem({y:290,name:'目标用户',count:2,preview:'你好'})
 row.children!.unshift({tag:'#text',text:'置顶',bounds:[264,291,28,14]})
 return {tag:'DIV',cls:'geek-item-wrap',bounds:[198,288,339,82],children:[row]}
}

test('广告首项、包装层和置顶提示不参与会话姓名/未读解析',async()=>{
 const snap=yangSnapshot({listItems:[ad(),pinned(),ad()]})
 assert.deepEqual(conversationListNamesOf(snap),['目标用户'])
 const read=await new ChatReadExecutor({snapshot:async()=>snap}).read()
 assert.deepEqual(read.unread.map(x=>({name:x.name,count:x.count})),[{name:'目标用户',count:2}])
 assert.equal(read.unread[0]?.lastPreview,'你好')
})
test('列表打开只点击真实会话，广告中的同名文字不造成歧义',async()=>{
 const snap=yangSnapshot({listItems:[ad(),pinned()]});let i=0;const clicks:unknown[]=[]
 const ex=new ChatOpenExecutor({snapshot:async()=>i++<2?snap:yangSnapshot({contact:'目标用户'}),click:async p=>{clicks.push(p)},clickAndType:async()=>{throw Error('unexpected typing')},sleep:async()=>{}})
 assert.equal((await ex.open({contact:'目标用户'})).via,'list')
 assert.equal(clicks.length,1)
})
test('缺少 user-list 时不从全页收集类似会话的组件',()=>{
 assert.deepEqual(conversationListNamesOf(buildChatSnapshot([pinned()])),[])
})
test('主文档搜索入口不受 iframe 内批量及图标污染',()=>{
 const s=buildChatSnapshot([{tag:'#text',text:'批量',bounds:[510,171,26,15]},{tag:'DIV',bounds:[503,124,34,34]}])
 const iframe=buildChatSnapshot([{tag:'#text',text:'批量',bounds:[510,171,26,15]},{tag:'DIV',bounds:[528,124,20,20]}])
 const shift=s.strings.length;s.strings.push(...iframe.strings)
 const d=iframe.documents[0]!
 d.nodes.nodeName=(d.nodes.nodeName as number[]).map(n=>n+shift)
 d.nodes.nodeValue=(d.nodes.nodeValue as number[]).map(n=>n<0?n:n+shift)
 s.documents.push(d)
 const ex=new ChatSearchExecutor({snapshot:async()=>s,click:async()=>{},clickAndType:async()=>{}})
 assert.deepEqual(ex.locateSearchEntry(s),{point:{x:520,y:141},count:1})
})

// 这里检查各定位阶段的输入证据，防止无真实鼠标的 fixture 掩盖跨文档污染。
function searchProbe(snap: ReturnType<typeof buildChatSnapshot>) {
 return new ChatSearchExecutor({snapshot:async()=>snap,click:async()=>{},clickAndType:async()=>{}}) as unknown as {
   findSearchField(s:typeof snap,p:{x:number;y:number}):{point:unknown;count:number}
   missingCharsOf(s:typeof snap,n:string,p:{x:number;y:number}):string[]
   locateTargetResult(s:typeof snap):{point:unknown;count:number}
 }
}
const searchInput=(value?:string):FixtureNode=>({tag:'INPUT',bounds:[248,126,240,30],inputValue:value})
test('输入必须在搜索框内精确落地，完整串 INPUT 值无需逐字 strings',()=>{
 const s=buildChatSnapshot([searchInput('张三')])
 assert.deepEqual(searchProbe(s).missingCharsOf(s,'张三',{x:520,y:141}),[])
})
test('空值 -1、残字、错误顺序和框外姓名字符均不能证明输入成功',()=>{
 for(const v of [undefined,'张','三张','张三四']){
  const s=buildChatSnapshot([searchInput(v),{tag:'#text',text:'张',bounds:[300,210,16,16]},{tag:'#text',text:'三',bounds:[318,210,16,16]}])
  assert.ok(searchProbe(s).missingCharsOf(s,'张三',{x:520,y:141}).length)
 }
})
test('旧版 DIV 输入只接受框内完整文字，框外相同字符无效',()=>{
 const build=(y:number)=>buildChatSnapshot([{tag:'DIV',bounds:[248,126,240,30]},{tag:'#text',text:'张三',bounds:[260,y,32,16]}])
 assert.deepEqual(searchProbe(build(130)).missingCharsOf(build(130),'张三',{x:520,y:141}),[])
 assert.ok(searchProbe(build(210)).missingCharsOf(build(210),'张三',{x:520,y:141}).length)
})
test('搜索框、联系人标签与结果公司名仅使用主文档，不能读 iframe 同名结果',()=>{
 const main=buildChatSnapshot([]), iframe=buildChatSnapshot([searchInput('张三'),{tag:'#text',text:'联系人',bounds:[206,173,60,26]},{tag:'#text',text:'_公司',bounds:[304,210,200,16]}])
 const shift=main.strings.length;main.strings.push(...iframe.strings)
 const d=iframe.documents[0]!
 d.nodes.nodeName=(d.nodes.nodeName as number[]).map(n=>n+shift)
 d.nodes.nodeValue=(d.nodes.nodeValue as number[]).map(n=>n<0?n:n+shift)
 main.documents.push(d)
 assert.equal(searchProbe(main).findSearchField(main,{x:520,y:141}).count,0)
 assert.equal(searchProbe(main).locateTargetResult(main).count,0)
 assert.ok(searchProbe(main).missingCharsOf(main,'张三',{x:520,y:141}).length)
})

test('头部姓名高亮拆成普通文本和嵌套末字时，验证完整身份',()=>{
 const nameBox:FixtureNode={tag:'SPAN',cls:'name-box',bounds:[598,126,54,26],children:[
  {tag:'#text',text:'张小',bounds:[598,129,36,20]},
  {tag:'SPAN',cls:'hightrare',bounds:[634,126,18,26],children:[{tag:'#text',text:'明',bounds:[634,129,18,20]}]},
 ]}
 const header=(children:FixtureNode[])=>buildChatSnapshot([{tag:'DIV',cls:'base-info-single-container',bounds:[568,110,772,139],children}])
 assert.equal(headerContactOf(header([nameBox,{tag:'#text',text:'查看简历',bounds:[700,129,60,20]}])),'张小明')
 assert.equal(headerContactOf(header([nameBox,nameBox])),'')
})
test('目标行仍在页面内但已滚到列表上方时，必须先滚动再点击',async()=>{
 const snap=yangSnapshot({listItems:[conversationItem({y:110,name:'目标用户'})]})
 const shown=yangSnapshot({listItems:[conversationItem({y:300,name:'目标用户'})]})
 const queue=[snap,snap,shown,yangSnapshot({contact:'目标用户'})];let i=0;const actions:string[]=[]
 const ex=new ChatOpenExecutor({snapshot:async()=>queue[Math.min(i++,queue.length-1)]!,click:async()=>{actions.push('click')},clickAndType:async()=>{throw Error('unexpected typing')},mouseWheel:async(_x,_y,dy)=>{assert.ok(dy<0);actions.push('wheel')},sleep:async()=>{}})
 assert.equal((await ex.open({contact:'目标用户'})).via,'list')
 assert.deepEqual(actions,['wheel','click'])
})
