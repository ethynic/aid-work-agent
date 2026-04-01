import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'

// 客户信息页面
import CustomerInfo from './components/CustomerInfo.vue'

// 定时任务管理页面
import ScheduledTasks from './components/ScheduledTasks.vue'

// 知识库管理页面
import KnowledgeBase from './components/KnowledgeBase.vue'

// 创建路由
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'chat',
      component: () => import('./components/ChatContainer.vue')
    },
    {
      path: '/customer-info',
      name: 'customer-info',
      component: CustomerInfo
    },
    {
      path: '/scheduled-tasks',
      name: 'scheduled-tasks',
      component: ScheduledTasks
    },
    {
      path: '/knowledge-base',
      name: 'knowledge-base',
      component: KnowledgeBase
    }
  ]
})

createApp(App).use(router).mount('#app')
