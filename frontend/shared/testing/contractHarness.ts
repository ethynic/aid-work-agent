import { describe, expect, it } from 'vitest'

export interface ContractCase<Input, Output> {
  name: string
  input: Input
  expected: Output
}

/** 为 Web/Desktop 对同一纯逻辑实现复用完全相同的 contract cases。 */
export function defineContractSuite<Input, Output>(
  name: string,
  implementation: (input: Input) => Output,
  cases: readonly ContractCase<Input, Output>[],
): void {
  describe(name, () => {
    it.each(cases)('$name', ({ input, expected }) => {
      expect(implementation(input)).toEqual(expected)
    })
  })
}
