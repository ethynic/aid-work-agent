export function isValidReleaseVersion(version: string): boolean {
  return /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$/.test(version)
}

export function assertReleaseSigning(mode: string, environment: Record<string, string | undefined>): void {
  if (!['dev', 'release'].includes(mode)) throw new Error('package mode must be dev or release')
  if (mode === 'release' && !(environment.CSC_LINK || environment.WIN_CSC_LINK)) {
    throw new Error('release packaging requires CSC_LINK or WIN_CSC_LINK; unsigned release is forbidden')
  }
}

export function assertPackageInvocation(mode: string, extraArguments: readonly string[]): void {
  if (!['dev', 'release'].includes(mode)) throw new Error('package mode must be dev or release')
  if (extraArguments.length > 0) {
    throw new Error('package post-processing cannot be run separately; rerun the standard packaging command')
  }
}

export function expectedSignature(mode: string): 'Valid' | 'NotSigned' {
  return mode === 'release' ? 'Valid' : 'NotSigned'
}
