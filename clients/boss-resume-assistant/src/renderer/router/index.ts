import { createRouter, createWebHistory } from 'vue-router'
import LoginGate from '../views/LoginGate.vue'
import JobConfig from '../views/JobConfig.vue'
import RunConsole from '../views/RunConsole.vue'
import ReviewQueue from '../views/ReviewQueue.vue'
import AuditLog from '../views/AuditLog.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/login' },
    { path: '/login', name: 'login', component: LoginGate },
    { path: '/jobs', name: 'jobs', component: JobConfig },
    { path: '/run', name: 'run', component: RunConsole },
    { path: '/review', name: 'review', component: ReviewQueue },
    { path: '/audit', name: 'audit', component: AuditLog },
    { path: '/:pathMatch(.*)*', redirect: '/login' },
  ],
})
