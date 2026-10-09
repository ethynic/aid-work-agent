/** Private trusted Main/Host platform bridge; never part of renderer H1. */
export interface SelectedPackageInput {
  selection_ref: string
  request_key: string
  instance_id: string
}
export interface SelectedPackageSnapshot {
  /** Main-owned immutable staging file. This is never supplied by renderer. */
  staged_path: string
  size: number
  /** SHA-256 hex of the entire outer .aidplugin.zip, not envelope.package_digest. */
  sha256: string
}
export interface RuntimePlatform {
  takeSelectedPackage(request: SelectedPackageInput): Promise<SelectedPackageSnapshot>
}
export interface RuntimePlatformRequest {
  kind: 'runtime_platform_request'
  id: string
  method: 'takeSelectedPackage'
  params: SelectedPackageInput
}
export interface RuntimePlatformResponse {
  kind: 'runtime_platform_response'
  id: string
  code: number
  error: string
  result: SelectedPackageSnapshot | null
}
