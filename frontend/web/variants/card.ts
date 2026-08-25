import { tv, type VariantProps } from 'tailwind-variants'

export const card = tv({
  slots: {
    wrapper: 'rounded-xl border border-default bg-surface shadow-sm',
    header: 'px-6 py-4 border-b border-default',
    title: 'text-lg font-semibold text-default',
    body: 'px-6 py-4',
    footer: 'px-6 py-4 border-t border-default',
  },
  variants: {
    padding: {
      sm: { body: 'px-4 py-3' },
      md: { body: 'px-6 py-4' },
      lg: { body: 'px-8 py-6' },
    },
    hoverable: {
      true: { wrapper: 'hover:shadow-md transition-shadow cursor-pointer' },
    },
  },
  defaultVariants: {
    padding: 'md',
  },
})

export type CardVariants = VariantProps<typeof card>
