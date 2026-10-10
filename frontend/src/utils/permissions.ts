import { decodeJwtPayload } from './jwt'

/** Roles the backend lets change data; every other role (viewers) may only look. */
const WRITE_ROLES = new Set(['admin', 'internal'])

/**
 * Whether the logged-in user may change content and configurations.
 *
 * Viewers, including the ones Google sign-in registers, get a read-only UI. The backend
 * enforces the same rule, so this only hides controls that would be refused.
 */
export function hasWriteAccess(token: string | null = localStorage.getItem('token')): boolean {
  const role = token ? decodeJwtPayload(token)?.role : undefined
  return typeof role === 'string' && WRITE_ROLES.has(role)
}
