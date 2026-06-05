import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
    },
    {
      path: '/',
      component: () => import('../layouts/AppLayout.vue'),
      meta: { requiresAuth: true },
      children: [
        {
          path: '',
          name: 'dashboard',
          component: () => import('../views/DashboardView.vue'),
        },
        {
          path: 'categories',
          component: () => import('../views/CategoriesView.vue'),
        },
        {
          path: 'categories/:categoryId',
          name: 'category',
          component: () => import('../views/CategoriesView.vue'),
        },
        {
          path: 'categories/:categoryId/items/:itemId',
          name: 'item-detail',
          component: () => import('../views/ItemDetailView.vue'),
        },
      ],
    },
  ],
})

router.beforeEach((to, _from, next) => {
  const isAuthenticated = !!localStorage.getItem('token')
  if (to.meta.requiresAuth && !isAuthenticated) {
    next({ name: 'login' })
  } else {
    next()
  }
})

export default router
