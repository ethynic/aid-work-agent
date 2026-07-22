import WebSocket from 'ws';
import { assertMethodAllowed } from './policy.mjs';
import { summarizeParams } from './audit.mjs';

export class RawCdpClient {
  constructor({ timeoutMs = 5000, auditWriter, WebSocketImpl = WebSocket } = {}) {
    this.timeoutMs = timeoutMs;
    this.auditWriter = auditWriter;
    this.WebSocketImpl = WebSocketImpl;
    this.nextId = 1;
    this.pending = new Map();
  }

  async connect(webSocketUrl) {
    if (this.socket) throw new Error('CDP client is already connected');
    const socket = new this.WebSocketImpl(webSocketUrl);
    this.socket = socket;
    try {
      await new Promise((resolve, reject) => {
        socket.once('open', resolve);
        socket.once('error', reject);
      });
    } catch (error) {
      if (this.socket === socket) this.socket = undefined;
      throw error;
    }
    socket.on('message', (data) => this.#onMessage(data));
    socket.on('error', (error) => this.#rejectAll(new Error(`CDP WebSocket error: ${error.message}`)));
    socket.on('close', () => {
      if (this.socket === socket) this.socket = undefined;
      this.#rejectAll(new Error('CDP WebSocket disconnected'));
    });
  }

  async send(method, params = {}, sessionId) {
    const startedAt = Date.now();
    try {
      assertMethodAllowed(method);
    } catch (error) {
      await this.#audit(method, params, sessionId, startedAt, 'FORBIDDEN');
      throw error;
    }
    if (!this.socket) throw new Error('CDP client is not connected');
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP request timed out: ${method}`));
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer, sessionId });
      try {
        this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
    try {
      const value = await result;
      await this.#audit(method, params, sessionId, startedAt, 'OK');
      return value;
    } catch (error) {
      await this.#audit(method, params, sessionId, startedAt, 'ERROR');
      throw error;
    }
  }

  async close() {
    if (!this.socket) return;
    const socket = this.socket;
    this.socket = undefined;
    socket.close();
    if (socket.readyState !== socket.CLOSED) {
      await new Promise((resolve) => {
        const onClose = () => { clearTimeout(timer); resolve(); };
        const timer = setTimeout(() => {
          socket.off('close', onClose);
          socket.terminate?.();
          resolve();
        }, this.timeoutMs);
        socket.once('close', onClose);
      });
    }
    this.#rejectAll(new Error('CDP client closed'));
  }

  #onMessage(data) {
    let message;
    try { message = JSON.parse(data.toString()); } catch { return; }
    const pending = this.pending.get(message.id);
    if (!pending) return;
    if (pending.sessionId !== message.sessionId) {
      clearTimeout(pending.timer);
      this.pending.delete(message.id);
      pending.reject(new Error(`CDP response session mismatch for request ${message.id}`));
      return;
    }
    clearTimeout(pending.timer);
    this.pending.delete(message.id);
    if (message.error) pending.reject(new Error(`CDP error ${message.error.code}: ${message.error.message}`));
    else pending.resolve(message.result);
  }

  #rejectAll(error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  async #audit(method, params, sessionId, startedAt, status) {
    if (!this.auditWriter) return;
    await this.auditWriter.write({ at: new Date().toISOString(), method, sessionId, params: summarizeParams(method, params), durationMs: Date.now() - startedAt, status });
  }
}
