import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/preview',
    },
    {
      path: '/preview',
      name: 'preview',
      component: () => import('../App.vue'),
      meta: { isAdmin: false },
    },
    {
      path: '/admin',
      name: 'admin',
      component: () => import('../App.vue'),
      meta: { isAdmin: true },
    },
  ],
})

export default router
