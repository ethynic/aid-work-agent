// Runtime-specific cleanup is installed by the entry point, never by shared API code.
export interface LogoutPreparation { headers: Record<string, string>; complete: (success: boolean) => Promise<void> }
let prepare: (() => Promise<LogoutPreparation>) | undefined
export function installLogoutHook(hook: () => Promise<LogoutPreparation>): void { prepare = hook }
export async function prepareLogout(): Promise<LogoutPreparation> {
  try { return prepare ? await prepare() : { headers: {}, complete: async () => {} } }
  catch { return { headers: {}, complete: async () => {} } }
}
