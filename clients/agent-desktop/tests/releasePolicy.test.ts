import assert from 'node:assert/strict'
import test from 'node:test'

import { assertPackageInvocation, assertReleaseSigning, expectedSignature, isValidReleaseVersion } from '../electron/releasePolicy.js'

test('release version accepts SemVer and rejects mutable/non-version labels', () => {
  assert.equal(isValidReleaseVersion('1.2.3'), true)
  assert.equal(isValidReleaseVersion('1.2.3-rc.1'), true)
  assert.equal(isValidReleaseVersion('latest'), false)
  assert.equal(isValidReleaseVersion('01.2.3'), false)
})

test('packaging cannot reuse artifacts after an unconfirmed builder result', () => {
  assert.doesNotThrow(() => assertPackageInvocation('dev', []))
  assert.throws(
    () => assertPackageInvocation('dev', ['--postprocess-existing']),
    /rerun the standard packaging command/,
  )
})

test('release packaging fails closed without a signing certificate while dev is explicitly unsigned', () => {
  assert.doesNotThrow(() => assertReleaseSigning('dev', {}))
  assert.throws(() => assertReleaseSigning('release', {}), /unsigned release is forbidden/)
  assert.doesNotThrow(() => assertReleaseSigning('release', { CSC_LINK: 'certificate-reference' }))
  assert.equal(expectedSignature('dev'), 'NotSigned')
  assert.equal(expectedSignature('release'), 'Valid')
})
