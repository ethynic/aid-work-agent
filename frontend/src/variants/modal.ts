import { tv, type VariantProps } from 'tailwind-variants'

export const modal = tv({
  slots: {
    overlay: 'fixed inset-0 z-50 flex items-center justify-center bg-black/40',
    content: 'bg-white rounded-xl shadow-xl w-full overflow-hidden',
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
      xl: { content: 'max-w-4xl' },
    },
    scrollable: {
      true: { body: 'max-h-[70vh]' },
    },
  },
  defaultVariants: {
    size: 'md',
    scrollable: true,
  },
})

export type ModalVariants = VariantProps<typeof modal>
