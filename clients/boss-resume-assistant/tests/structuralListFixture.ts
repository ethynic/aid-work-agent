import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

export interface StructuralRow {
  name: string | null
  button: [number, number, number, number]
  nameBounds?: [number, number, number, number]
  continued?: boolean
}

/** List identity comes from card ancestry; coordinates remain explicit for DPI and stale-point regressions. */
export function structuralListSnapshot(rows: StructuralRow[], width: number, height: number,
  extraTexts: string[] = []): DomSnapshot {
  const strings = [''], values: number[] = [], tags: number[] = [], parents: number[] = [], attributes: number[][] = []
  const indices: number[] = [], bounds: number[][] = []
  const str = (text: string): number => { let index = strings.indexOf(text); if (index < 0) { index = strings.length; strings.push(text) }; return index }
  const add = (tag: string, parent: number, cls: string, box: number[], text = ''): number => {
    const index = values.length
    values.push(str(text)); tags.push(str(tag)); parents.push(parent)
    attributes.push(cls ? [str('class'), str(cls)] : [])
    indices.push(index); bounds.push(box)
    return index
  }
  const root = add('HTML', -1, '', [0, 0, width, height])
  add('#text', root, '', [100, 50, 80, 24], '筛选')
  for (const text of extraTexts) add('#text', root, '', [100, 300, 150, 20], text)
  const wrap = add('DIV', root, 'list-wrap card-list-wrap', [0, 0, width, 20000])
  const body = add('DIV', wrap, 'list-body', [0, 0, width, 20000])
  const list = add('DIV', body, 'card-list', [0, 0, width, 20000])
  for (const row of rows) {
    const y = row.button[1] + row.button[3] / 2
    const card = add('DIV', list, 'card-item', [0, y - 40, width, 160])
    const nameBox = row.nameBounds ?? [317, y - 18, 50, 20]
    const nameWrap = add('DIV', card, 'row name-wrap', [nameBox[0], nameBox[1], 500, 40])
    if (row.name !== null) {
      const name = add('SPAN', nameWrap, 'name', nameBox)
      add('#text', name, '', nameBox, row.name)
    }
    add('#text', nameWrap, '', [370, y - 18, 60, 20], '刚刚活跃')
    add('#text', card, '', [317, y + 30, 40, 20], '本科')
    const button = add('BUTTON', card, row.continued ? 'btn btn-continue' : 'btn btn-greet', row.button)
    // Keep text last: legacy precision tests adjust this final bounds entry.
    add('#text', button, '', [...row.button], row.continued ? '继续沟通' : '\n                  打招呼')
  }
  return { strings, documents: [{ nodes: { nodeValue: values, nodeName: tags, parentIndex: parents,
    attributes, contentDocumentIndex: values.map(() => -1) },
    layout: { nodeIndex: indices, bounds, clientRects: bounds.map(box => [...box]) }, scrollOffsetY: 0 }] }
}
