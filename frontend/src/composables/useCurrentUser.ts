import type { User } from '../types'
import { decodeJwtPayload } from '../utils/jwt'

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function initialsFrom(name: string): string {
  const parts = name.split(/[^a-zA-Z0-9]+/).filter(Boolean)
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : name.slice(0, 2)
  return letters.toUpperCase() || '?'
}

/** Data-URI circular avatar carrying the user's initials — no photo exists server-side. */
function initialsAvatar(initials: string): string {
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">' +
    '<rect width="64" height="64" rx="32" fill="#4f46e5"/>' +
    `<text x="32" y="33" dy=".35em" text-anchor="middle" font-family="sans-serif" font-size="24" fill="#fff">${initials}</text>` +
    '</svg>'
  return `data:image/svg+xml,${encodeURIComponent(svg)}`
}

/** Derives the logged-in user's display name/role/avatar from the JWT stored at login. */
export function useCurrentUser(): User {
  const token = localStorage.getItem('token')
  const payload = token ? decodeJwtPayload(token) : null
  const name = payload?.sub ?? 'Unknown'
  const role = payload?.role ? capitalize(payload.role) : ''

  return {
    name,
    role,
    avatarUri: initialsAvatar(initialsFrom(name)),
  }
}
