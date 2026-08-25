import { ref, computed, type Ref } from 'vue'

/**
 * 弹框关闭时的脏检测守卫
 *
 * 用于新增/编辑弹框关闭时，检测是否有未保存的修改，并弹出确认对话框。
 *
 * @param form - 当前表单数据的 ref
 * @param getInitialSnapshot - 返回表单初始值快照的函数（在弹框打开时调用）
 * @param onSave - 保存回调（返回 Promise），保存成功后关闭弹框
 *
 * @example
 * ```ts
 * const form = ref({ name: '', age: 0 })
 * const { openModal, handleOverlayClick, handleCloseClick, showConfirm, confirmMessage, confirmSave, confirmDiscard, confirmCancel } =
 *   useModalCloseGuard(form, () => ({ ...form.value }), async () => { await save() })
 *
 * // 在 BaseModal 上绑定
 * <BaseModal v-model="showModal" :closeOnOverlay="false" @overlay-click="handleOverlayClick">
 *   <template #footer>
 *     <BaseButton intent="secondary" @click="handleCloseClick">取消</BaseButton>
 *     <BaseButton @click="confirmSave">保存</BaseButton>
 *   </template>
 * </BaseModal>
 * ```
 */
export function useModalCloseGuard<T extends Record<string, any>>(
  form: Ref<T>,
  getInitialSnapshot: () => T,
  onSave: () => Promise<void>,
) {
  const showModal = ref(false)
  const initialSnapshot = ref<T>(getInitialSnapshot())
  const showConfirm = ref(false)
  const pendingClose = ref<(() => void) | null>(null)

  const confirmMessage = '当前页面有未保存的修改，是否保存？'

  // 是否脏（有未保存的修改）
  const isDirty = computed(() => {
    const current = form.value
    const initial = initialSnapshot.value
    return JSON.stringify(current) !== JSON.stringify(initial)
  })

  // 打开弹框时记录初始快照
  function openModal() {
    initialSnapshot.value = getInitialSnapshot()
    showModal.value = true
    showConfirm.value = false
  }

  // 关闭弹框
  function closeModal() {
    showModal.value = false
    showConfirm.value = false
  }

  // 请求关闭（先检测是否有脏数据）
  function requestClose(afterClose?: () => void) {
    if (isDirty.value) {
      pendingClose.value = () => {
        closeModal()
        afterClose?.()
      }
      showConfirm.value = true
    } else {
      closeModal()
      afterClose?.()
    }
  }

  // 点击遮罩层
  function handleOverlayClick() {
    requestClose()
  }

  // 点击关闭/取消按钮
  function handleCloseClick() {
    requestClose()
  }

  // 确认对话框：保存
  async function confirmSave() {
    showConfirm.value = false
    try {
      await onSave()
      if (pendingClose.value) {
        pendingClose.value()
        pendingClose.value = null
      } else {
        closeModal()
      }
    } catch (e) {
      console.error('保存失败', e)
      alert('保存失败，请重试')
    }
  }

  // 确认对话框：不保存（放弃修改）
  function confirmDiscard() {
    showConfirm.value = false
    if (pendingClose.value) {
      pendingClose.value()
      pendingClose.value = null
    } else {
      closeModal()
    }
  }

  // 确认对话框：取消（停留）
  function confirmCancel() {
    showConfirm.value = false
    pendingClose.value = null
  }

  return {
    showModal,
    showConfirm,
    confirmMessage,
    isDirty,
    openModal,
    closeModal,
    requestClose,
    handleOverlayClick,
    handleCloseClick,
    confirmSave,
    confirmDiscard,
    confirmCancel,
  }
}
