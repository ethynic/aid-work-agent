import assert from 'node:assert/strict';
import test from 'node:test';
import { summarizeParams } from '../src/audit.mjs';

test('audit summaries omit response-like private payloads while retaining protocol evidence', () => {
  assert.deepEqual(summarizeParams('Network.getResponseBody', { requestId: 'r1', body: 'resume', cookie: 'secret' }), { requestId: 'r1' });
});

test('audit drops injected source text because forbidden requests must not leak payloads', () => {
  const summary = summarizeParams('Page.addScriptToEvaluateOnNewDocument', { source: 'private', runImmediately: true });
  assert.deepEqual(summary, { runImmediately: true });
});

test('audit recursively redacts nested credentials and resume payloads', () => {
  const summary = summarizeParams('Network.enable', {
    headers: { Authorization: 'Bearer secret', Cookie: 'sid=secret', Accept: 'application/json' },
    nested: { responseBody: 'candidate resume', securityId: 'private', requestId: 'r1' },
  });
  assert.deepEqual(summary, { headers: { Accept: 'application/json' }, nested: { requestId: 'r1' } });
});
