import { createRouter, createWebHistory } from 'vue-router'
import Placeholder from '../views/Placeholder.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'placeholder', component: Placeholder },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
