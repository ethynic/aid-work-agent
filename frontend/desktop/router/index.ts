import { createRouter, createWebHistory, type Router } from 'vue-router'
import DesktopHomePage from '@desktop/pages/DesktopHomePage.vue'

export function createDesktopRouter(): Router {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/', name: 'desktop-home', component: DesktopHomePage },
      { path: '/:pathMatch(.*)*', redirect: '/' },
    ],
  })
}
