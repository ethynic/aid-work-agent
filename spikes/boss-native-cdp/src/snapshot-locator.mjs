export const WRITE_ACTION_TEXT = /打招呼|沟通|不合适|不感兴趣|发送|确认|确定|邀约|约面|交换|电话|微信/i;

export function assertSafeCandidateText(expectedText) {
  if (typeof expectedText !== 'string' || !expectedText.trim() || WRITE_ACTION_TEXT.test(expectedText)) {
    throw new Error('Candidate text is empty or resembles a recruiting write action');
  }
}

export function locateUniqueText(snapshot, expectedText) {
  assertSafeCandidateText(expectedText);
  const stringIndex = snapshot.strings.indexOf(expectedText);
  if (stringIndex < 0) throw new Error('Candidate text is absent from the baseline snapshot');
  const matches = [];
  snapshot.documents.forEach((document, documentIndex) => {
    indexedValues(document.nodes.contentDocumentIndex, 'contentDocumentIndex');
    indexedValues(document.nodes.nodeValue, 'nodeValue').forEach(([nodeIndex, value]) => {
      if (value !== stringIndex) return;
      const layoutIndex = document.layout.nodeIndex.indexOf(nodeIndex);
      if (layoutIndex >= 0) matches.push({ documentIndex, bounds: document.layout.bounds[layoutIndex] });
    });
  });
  if (matches.length !== 1) throw new Error(`Candidate text must have exactly one visible match; found ${matches.length}`);

  const match = matches[0];
  let documentIndex = match.documentIndex;
  let offsetX = 0;
  let offsetY = 0;
  const visited = new Set();
  while (documentIndex !== 0) {
    if (visited.has(documentIndex)) throw new Error('Candidate frame ancestry contains a cycle');
    visited.add(documentIndex);
    const owners = [];
    snapshot.documents.forEach((document, ownerDocumentIndex) => {
      indexedValues(document.nodes.contentDocumentIndex, 'contentDocumentIndex').forEach(([nodeIndex, value]) => {
        if (value === documentIndex) owners.push({ document, ownerDocumentIndex, nodeIndex });
      });
    });
    if (owners.length !== 1) throw new Error(`Candidate frame must have exactly one visible owner; found ${owners.length}`);
    const owner = owners[0];
    const layoutIndex = owner.document.layout.nodeIndex.indexOf(owner.nodeIndex);
    if (layoutIndex < 0) throw new Error('Candidate frame has no visible layout bounds');
    const [x, y] = owner.document.layout.bounds[layoutIndex];
    offsetX += x;
    offsetY += y;
    documentIndex = owner.ownerDocumentIndex;
  }

  const [x, y, width, height] = match.bounds;
  const point = { x: offsetX + x + width / 2, y: offsetY + y + height / 2 };
  if (![x, y, width, height, point.x, point.y].every(Number.isFinite)
      || width <= 0 || height <= 0 || point.x < 0 || point.y < 0) {
    throw new Error('Candidate text has no safe visible click area');
  }
  return point;
}

function indexedValues(field, name) {
  if (Array.isArray(field)) return field.map((value, index) => [index, value]);
  if (field && Array.isArray(field.index) && Array.isArray(field.value)
      && field.index.length === field.value.length) {
    return field.index.map((index, position) => [index, field.value[position]]);
  }
  throw new Error(`Unsupported DOMSnapshot ${name} shape`);
}
