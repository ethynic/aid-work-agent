export class BrowserFailure extends Error {
  constructor(readonly code: string, message: string) { super(message) }
}
