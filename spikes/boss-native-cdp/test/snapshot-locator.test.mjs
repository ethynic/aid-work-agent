import test from 'node:test';
import assert from 'node:assert/strict';
import { locateUniqueText } from '../src/snapshot-locator.mjs';

function snapshot({ dense = false, duplicate = false } = {}) {
  const nodeValue = dense ? [-1, 1, ...(duplicate ? [1] : [])] : { index: duplicate ? [1, 2] : [1], value: duplicate ? [1, 1] : [1] };
  return {
    strings: ['', '候选甲'],
    documents: [
      {
        nodes: { nodeValue: [-1], contentDocumentIndex: dense ? [1] : { index: [0], value: [1] } },
        layout: { nodeIndex: [0], bounds: [[168, 40, 1000, 900]] },
      },
      {
        nodes: { nodeValue, contentDocumentIndex: dense ? [-1, -1, -1] : { index: [], value: [] } },
        layout: { nodeIndex: duplicate ? [1, 2] : [1], bounds: duplicate ? [[150, 89, 48, 17], [200, 89, 48, 17]] : [[150, 89, 48, 17]] },
      },
    ],
  };
}

test('locates one visible candidate in sparse DOMSnapshot data', () => {
  assert.deepEqual(locateUniqueText(snapshot(), '候选甲'), { x: 342, y: 137.5 });
});

test('supports Chrome dense-array DOMSnapshot serialization', () => {
  assert.deepEqual(locateUniqueText(snapshot({ dense: true }), '候选甲'), { x: 342, y: 137.5 });
});

test('rejects ambiguous candidate text instead of guessing', () => {
  assert.throws(() => locateUniqueText(snapshot({ duplicate: true }), '候选甲'), /exactly one visible match/);
});

test('rejects recruiting write-action labels', () => {
  assert.throws(() => locateUniqueText(snapshot(), '打招呼'), /write action/);
});
