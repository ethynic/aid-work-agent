import { tv, type VariantProps } from 'tailwind-variants'

export const button = tv({
  base: 'inline-flex items-center justify-center rounded-lg font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none',
  variants: {
    intent: {
      primary: 'bg-primary-600 text-white hover:bg-primary-700 focus:ring-primary-500',
      secondary: 'bg-gray-100 text-gray-700 hover:bg-gray-200 focus:ring-gray-400',
      danger: 'bg-danger-600 text-white hover:bg-danger-700 focus:ring-danger-500',
      'danger-ghost': 'text-danger-600 hover:bg-danger-50 focus:ring-danger-500',
      ghost: 'text-gray-600 hover:bg-gray-100 hover:text-gray-900',
    },
    size: {
      sm: 'h-8 px-3 text-sm gap-1.5',
      md: 'h-10 px-4 text-sm gap-2',
      lg: 'h-12 px-6 text-base gap-2.5',
    },
    fullWidth: {
      true: 'w-full',
    },
  },
  defaultVariants: {
    intent: 'primary',
    size: 'md',
  },
})

export type ButtonVariants = VariantProps<typeof button>
