import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

export interface Card { name: string; y: number; sent?: boolean; hidden?: boolean; advantage?: string; baseInfo?: string; expectation?: string }
export function loadedSnapshot(cards: Card[], scroll = 0, boundaryAfter?: number): DomSnapshot {
  const strings = [''], str = (s: string) => { let i = strings.indexOf(s); if (i < 0) { i = strings.length; strings.push(s) }; return i }
  function doc(width: number, height: number) {
    const values: number[] = [], tags: number[] = [], parents: number[] = [], attrs: number[][] = [], frames: number[] = []
    const indices: number[] = [], bounds: number[][] = [], clients: number[][] = [], styles: number[][] = []
    const add = (tag: string, parent: number, cls: string, b: number[], value = '', hidden = false, position = 'static') => {
      const n = values.length; values.push(str(value)); tags.push(str(tag)); parents.push(parent); attrs.push(cls ? [str('class'), str(cls)] : []); frames.push(-1)
      indices.push(n); bounds.push(b); clients.push([0, 0, b[2]! / 2, b[3]! / 2]); styles.push(['visible','visible',hidden ? 'none' : 'block',position,'auto','visible','1'].map(str)); return n
    }
    add('#document', -1, '', [0,0,width,height])
    const d = { nodes: { nodeValue: values, nodeName: tags, parentIndex: parents, attributes: attrs, contentDocumentIndex: frames }, layout: { nodeIndex: indices, bounds, clientRects: clients, styles }, scrollOffsetY: 0 }
    return { d, add, frames }
  }
  const root = doc(1000,800); root.add('HTML',0,'',[0,0,1000,800]); const owner = root.add('IFRAME',1,'',[200,40,800,760]); root.frames[owner]=1
  const avatar=root.add('DIV',1,'avatar-content',[900,10,60,60]),image=root.add('IMG',avatar,'',[900,10,60,60])
  root.d.nodes.attributes[image] = [str('src'),str('https://example.com/test-account.png')]
  const inner = doc(800,760), html = inner.add('HTML',0,'',[0,0,800,760]), wrap = inner.add('DIV',html,'list-wrap card-list-wrap',[0,0,800,20000])
  const body = inner.add('DIV',wrap,'list-body',[0,0,800,20000]), list = inner.add('DIV',body,'card-list',[0,0,800,20000])
  const head=inner.add('DIV',html,'head-wrap',[0,scroll,800,120],'',false,'fixed')
  const selector=inner.add('DIV',head,'job-selecter-wrap',[250,scroll+20,300,30])
  const label=inner.add('SPAN',selector,'ui-dropmenu-label',[250,scroll+20,300,30])
  inner.add('#text',label,'',[250,scroll+20,300,30],'PHP 开发工程师 _ 上海 15-30K')
  for (const [ci, c] of cards.entries()) {
    if (ci === boundaryAfter) {
      const marker=inner.add('DIV',list,'card-item',[30,c.y-250,730,100])
      const component=inner.add('DIV',marker,'mome-main-ui',[30,c.y-250,730,100])
      const divider=inner.add('DIV',component,'recommend-mome-ui',[30,c.y-250,730,100])
      inner.add('#text',divider,'',[30,c.y-250,730,30],'暂无符合牛人，为你推荐')
    }
    const card = inner.add('DIV',list,'card-item',[30,c.y,730,220],'',c.hidden)
    const row = inner.add('DIV',card,'row name-wrap',[80,c.y+20,500,40]), name = inner.add('SPAN',row,'name',[80,c.y+20,80,30])
    inner.add('#text',name,'',[80,c.y+20,80,30],c.name)
    for (const [klass,text] of [['base-info',c.baseInfo],['expect-wrap',c.expectation]]) if (text) {
      const section=inner.add('DIV',card,klass!,[80,c.y+55,500,30])
      inner.add('#text',section,'',[80,c.y+55,500,30],text)
    }
    if (c.advantage !== undefined) {
      const row=inner.add('DIV',card,'row row-flex geek-desc',[80,c.y+100,500,40])
      const label=inner.add('SPAN',row,'label',[80,c.y+100,50,30])
      inner.add('#text',label,'',[80,c.y+100,50,30],'优势')
      const content=inner.add('SPAN',row,'content',[130,c.y+100,450,30])
      inner.add('#text',content,'',[130,c.y+100,450,30],c.advantage)
    }
    const actions=inner.add('SPAN',card,'btn-doc',[600,c.y+20,100,40])
    const buttons=inner.add('DIV',actions,'button-list',[600,c.y+20,100,40])
    const actionWrap=c.sent ? inner.add('DIV',buttons,'btn-continue-wrap',[600,c.y+20,100,40]) : buttons
    const button = inner.add('BUTTON',actionWrap,c.sent ? 'btn btn-continue btn-outline' : 'btn btn-greet',[600,c.y+20,100,40])
    inner.add('#text',button,'',[610,c.y+25,80,30],c.sent ? '继续沟通' : '打招呼')
  }
  inner.d.scrollOffsetY = scroll
  return { strings, documents: [root.d,inner.d] }
}
