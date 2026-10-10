// Entry points install runtime cleanup before credentials are replaced.
export interface LoginSubject { user_id: string; tenant_id: string | null }
export interface IdentityPreparation { complete: () => Promise<void> }
let prepare: ((next: LoginSubject) => Promise<IdentityPreparation>) | undefined
export function installIdentityChangeHook(hook: (next: LoginSubject) => Promise<IdentityPreparation>): void { prepare = hook }
export async function prepareIdentityChange(next: LoginSubject): Promise<IdentityPreparation> {
  return prepare ? prepare(next) : { complete: async () => {} }
}
