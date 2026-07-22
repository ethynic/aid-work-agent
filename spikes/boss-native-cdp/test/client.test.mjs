import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';
import { RawCdpClient } from '../src/client.mjs';
import { ForbiddenCdpMethodError } from '../src/policy.mjs';

class FakeSocket extends EventEmitter {
  static CLOSED = 3;
  constructor() { super(); this.CLOSED = 3; this.readyState = 1; queueMicrotask(() => this.emit('open')); }
  send(payload) { this.last = JSON.parse(payload); }
  close() { this.readyState = 3; queueMicrotask(() => this.emit('close')); }
}

class FailingSocket extends EventEmitter {
  constructor() { super(); queueMicrotask(() => this.emit('error', new Error('connect failed'))); }
}

class SendFailingSocket extends FakeSocket {
  send() { throw new Error('send failed'); }
}

class StalledCloseSocket extends FakeSocket {
  close() {}
  terminate() { this.terminated = true; this.readyState = this.CLOSED; }
}

test('Runtime methods fail before any packet is sent because detail refresh is a hard safety boundary', async () => {
  const client = new RawCdpClient({ WebSocketImpl: FakeSocket });
  await client.connect('ws://test');
  await assert.rejects(client.send('Runtime.enable'), ForbiddenCdpMethodError);
  assert.equal(client.socket.last, undefined);
  await client.close();
});

test('new-document script injection is forbidden because OCR is the only resume extraction path', async () => {
  const client = new RawCdpClient({ WebSocketImpl: FakeSocket });
  await client.connect('ws://test');
  await assert.rejects(client.send('Page.addScriptToEvaluateOnNewDocument', { source: 'alert(1)' }), ForbiddenCdpMethodError);
  assert.equal(client.socket.last, undefined);
  await client.close();
});

test('new-document script removal is forbidden because no script installation is allowed', async () => {
  const client = new RawCdpClient({ WebSocketImpl: FakeSocket });
  await client.connect('ws://test');
  await assert.rejects(client.send('Page.removeScriptToEvaluateOnNewDocument', { identifier: 'any-script' }), ForbiddenCdpMethodError);
  assert.equal(client.socket.last, undefined);
  await client.close();
});

test('forbidden methods are audited because a safety-boundary violation must fail loud', async () => {
  const entries = [];
  const client = new RawCdpClient({
    WebSocketImpl: FakeSocket,
    auditWriter: { write: async (entry) => entries.push(entry) },
  });
  await client.connect('ws://test');
  await assert.rejects(client.send('Debugger.enable', { token: 'secret' }, 'session-private'), ForbiddenCdpMethodError);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].method, 'Debugger.enable');
  assert.equal(entries[0].status, 'FORBIDDEN');
  assert.deepEqual(entries[0].params, {});
  await client.close();
});

test('request id and session routing preserve concurrent command correlation', async () => {
  const client = new RawCdpClient({ WebSocketImpl: FakeSocket });
  await client.connect('ws://test');
  const pending = client.send('Page.captureScreenshot', { format: 'png' }, 'session-a');
  assert.deepEqual(client.socket.last, { id: 1, method: 'Page.captureScreenshot', params: { format: 'png' }, sessionId: 'session-a' });
  client.socket.emit('message', JSON.stringify({ id: 1, result: { data: 'abc' }, sessionId: 'session-a' }));
  assert.deepEqual(await pending, { data: 'abc' });
  await client.close();
});

test('timeout rejects and clears pending work so a stalled browser cannot hang automation', async () => {
  const client = new RawCdpClient({ WebSocketImpl: FakeSocket, timeoutMs: 5 });
  await client.connect('ws://test');
  await assert.rejects(client.send('Target.getTargets'), /timed out/);
  assert.equal(client.pending.size, 0);
  await client.close();
});

test('out-of-order responses remain correlated while event storms are ignored', async () => {
  const client = new RawCdpClient({ WebSocketImpl: FakeSocket });
  await client.connect('ws://test');
  const first = client.send('Page.getFrameTree', {}, 'session-a');
  const second = client.send('Page.captureScreenshot', { format: 'png' }, 'session-a');
  for (let index = 0; index < 100; index += 1) {
    client.socket.emit('message', JSON.stringify({ method: 'Network.dataReceived', params: { requestId: String(index) }, sessionId: 'session-a' }));
  }
  client.socket.emit('message', JSON.stringify({ id: 2, result: { data: 'second' }, sessionId: 'session-a' }));
  client.socket.emit('message', JSON.stringify({ id: 1, result: { frameTree: 'first' }, sessionId: 'session-a' }));
  assert.deepEqual(await Promise.all([first, second]), [{ frameTree: 'first' }, { data: 'second' }]);
  await client.close();
});

test('disconnect rejects all pending requests and clears correlation state', async () => {
  const client = new RawCdpClient({ WebSocketImpl: FakeSocket });
  await client.connect('ws://test');
  const first = client.send('Page.getFrameTree', {}, 'session-a');
  const second = client.send('Target.getTargets');
  client.socket.emit('close');
  await assert.rejects(first, /disconnected/);
  await assert.rejects(second, /disconnected/);
  assert.equal(client.pending.size, 0);
  assert.equal(client.socket, undefined);
});

test('failed connection clears socket state so the user can retry after fixing Chrome', async () => {
  const client = new RawCdpClient({ WebSocketImpl: FailingSocket });
  await assert.rejects(client.connect('ws://test'), /connect failed/);
  assert.equal(client.socket, undefined);
});

test('socket errors reject pending requests instead of becoming an unhandled process error', async () => {
  const client = new RawCdpClient({ WebSocketImpl: FakeSocket });
  await client.connect('ws://test');
  const pending = client.send('Target.getTargets');
  client.socket.emit('error', new Error('transport failed'));
  await assert.rejects(pending, /WebSocket error: transport failed/);
  assert.equal(client.pending.size, 0);
  await client.close();
});

test('synchronous transport send failures clear request correlation immediately', async () => {
  const client = new RawCdpClient({ WebSocketImpl: SendFailingSocket });
  await client.connect('ws://test');
  await assert.rejects(client.send('Target.getTargets'), /send failed/);
  assert.equal(client.pending.size, 0);
  await client.close();
});

test('response from another session is rejected instead of being misrouted', async () => {
  const client = new RawCdpClient({ WebSocketImpl: FakeSocket });
  await client.connect('ws://test');
  const pending = client.send('Page.getFrameTree', {}, 'session-a');
  client.socket.emit('message', JSON.stringify({ id: 1, result: { frameTree: 'wrong' }, sessionId: 'session-b' }));
  await assert.rejects(pending, /session mismatch/);
  assert.equal(client.pending.size, 0);
  await client.close();
});

test('close is bounded and terminates a stalled transport so cleanup cannot hang the gate', async () => {
  const client = new RawCdpClient({ WebSocketImpl: StalledCloseSocket, timeoutMs: 5 });
  await client.connect('ws://test');
  const socket = client.socket;
  await client.close();
  assert.equal(socket.terminated, true);
  assert.equal(client.socket, undefined);
});
