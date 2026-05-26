import { tv, type VariantProps } from 'tailwind-variants'

export const pagination = tv({
  slots: {
    wrapper: 'flex items-center justify-between px-4 py-3 border-t border-default',
    info: 'text-sm text-muted',
    buttons: 'flex items-center gap-1',
    button: 'inline-flex items-center justify-center h-8 w-8 rounded-lg text-sm transition-colors',
    pageButton: 'inline-flex items-center justify-center h-8 min-w-[2rem] px-2 rounded-lg text-sm transition-colors',
  },
  variants: {
    active: {
      true: { pageButton: 'bg-primary-600 text-white font-medium' },
      false: { pageButton: 'text-default hover:bg-gray-100' },
    },
  },
})

export type PaginationVariants = VariantProps<typeof pagination>
