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
      redirect: '/categories',
    },
    {
      path: '/categories',
      component: () => import('../layouts/AppLayout.vue'),
      meta: { requiresAuth: true },
      children: [
        {
          path: '',
          name: 'categories',
          component: () => import('../views/CategoriesView.vue'),
        },
        {
          path: ':categoryId',
          name: 'category',
          component: () => import('../views/CategoriesView.vue'),
        },
        {
          path: ':categoryId/images/:imageId',
          name: 'image-detail',
          component: () => import('../views/ImageDetailView.vue'),
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
