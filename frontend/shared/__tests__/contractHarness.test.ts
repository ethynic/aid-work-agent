import { defineContractSuite } from '@shared/testing/contractHarness'

defineContractSuite(
  'shared contract harness',
  (value: string) => value.trim(),
  [
    { name: 'keeps an already normalized value', input: 'ready', expected: 'ready' },
    { name: 'normalizes consumer input', input: '  ready  ', expected: 'ready' },
  ],
)
