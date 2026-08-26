import { tv, type VariantProps } from 'tailwind-variants'

export const modal = tv({
  slots: {
    overlay: 'fixed inset-0 z-50 flex items-center justify-center bg-black/40',
    content: 'bg-white rounded-xl shadow-xl w-full max-h-[90vh] flex flex-col overflow-hidden',
    header: 'flex items-center justify-between px-6 py-4 border-b border-default',
    title: 'text-lg font-semibold text-default',
    body: 'px-6 py-4 overflow-y-auto',
    footer: 'flex items-center justify-end gap-3 px-6 py-4 border-t border-default',
    close: 'text-muted hover:text-default transition-colors',
  },
  variants: {
    size: {
      sm: { content: 'max-w-sm' },
      md: { content: 'max-w-lg' },
      lg: { content: 'max-w-2xl' },
      xl: { content: 'w-[90vw] h-[90vh]' },
    },
    scrollable: {
      true: { body: 'flex-1 min-h-0' },
    },
  },
  defaultVariants: {
    size: 'md',
    scrollable: true,
  },
})

export type ModalVariants = VariantProps<typeof modal>
