import { createRouter, createWebHistory } from 'vue-router'
import { hasWriteAccess } from '../utils/permissions'

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior(to, from, savedPosition) {
    if (!savedPosition && to.path === from.path) return false
    return savedPosition ?? { top: 0 }
  },
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
          component: () => import('../views/ItemDetailsView.vue'),
        },
        {
          path: 'configuration',
          name: 'configuration',
          component: () => import('../views/ConfigurationsView.vue'),
          meta: { navigation: 'configuration', requiresWriteAccess: true },
        },
        {
          path: 'configuration/:serviceName',
          name: 'service-configuration',
          component: () => import('../views/ServiceConfigurationView.vue'),
          meta: { navigation: 'configuration', requiresWriteAccess: true },
        },
      ],
    },
  ],
})

router.beforeEach((to, _from, next) => {
  const isAuthenticated = !!localStorage.getItem('token')
  if (to.meta.requiresAuth && !isAuthenticated) {
    next({ name: 'login' })
  } else if (to.meta.requiresWriteAccess && !hasWriteAccess()) {
    // Viewers cannot change configurations, so they never reach the configuration pages.
    next({ name: 'dashboard' })
  } else {
    next()
  }
})

export default router
