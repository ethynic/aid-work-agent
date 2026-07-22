export const ALLOWED_METHODS = new Set([
  'Target.getTargets',
  'Target.attachToTarget',
  'Target.detachFromTarget',
  'Page.enable',
  'Page.disable',
  'Page.getFrameTree',
  'Page.captureScreenshot',
  'Network.enable',
  'Network.disable',
  'Network.getResponseBody',
  'DOMSnapshot.captureSnapshot',
  'Input.dispatchMouseEvent',
  'Input.dispatchKeyEvent',
]);

export class ForbiddenCdpMethodError extends Error {}

export function assertMethodAllowed(method) {
  if (typeof method !== 'string' || method.startsWith('Runtime.') || !ALLOWED_METHODS.has(method)) {
    throw new ForbiddenCdpMethodError(`CDP method is forbidden: ${String(method)}`);
  }
}
