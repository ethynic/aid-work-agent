import { tv, type VariantProps } from 'tailwind-variants'

export const select = tv({
  base: 'w-full rounded-lg border bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500 disabled:bg-gray-50 disabled:text-muted',
  variants: {
    state: {
      default: 'border-default',
      error: 'border-danger-500 focus:ring-danger-500/20 focus:border-danger-500',
    },
    size: {
      sm: 'h-8 text-xs',
      md: 'h-10 text-sm',
      lg: 'h-12 text-base',
    },
  },
  defaultVariants: {
    state: 'default',
    size: 'md',
  },
})

export type SelectVariants = VariantProps<typeof select>
