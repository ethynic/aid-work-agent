import { tv, type VariantProps } from 'tailwind-variants'

export const table = tv({
  slots: {
    wrapper: 'w-full overflow-x-auto rounded-lg border border-default',
    table: 'w-full text-sm',
    thead: 'bg-gray-50',
    th: 'px-4 py-3 text-center text-xs font-bold text-muted uppercase tracking-wider',
    tbody: 'divide-y divide-default',
    tr: 'transition-colors',
    td: 'px-4 py-1 text-center text-default',
    empty: 'px-4 py-12 text-center text-muted',
  },
  variants: {
    stripe: {
      odd: {
        tr: 'bg-white hover:bg-surface-hover',
      },
      even: {
        tr: 'bg-primary-50 hover:bg-primary-100',
      },
    },
  },
})

export type TableVariants = VariantProps<typeof table>
