import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'

// 客户信息页面
import CustomerInfo from './components/CustomerInfo.vue'

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
    }
  ]
})

createApp(App).use(router).mount('#app')
