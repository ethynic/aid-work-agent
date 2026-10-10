import { ref } from 'vue'

// A layout sidebar can be a sibling of the chat. Publish intent before router guards
// or session selection, including a new conversation when the current ID is null.
export const sessionNavigationIntent = ref(0)
export function notifySessionNavigation(): void { sessionNavigationIntent.value += 1 }
