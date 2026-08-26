import { ref } from 'vue'

export const TECHNICAL_DEBUG_PASSWORD = '123456'
const ADMIN_ROLE_KEY = 'fish-quality-admin-role'
export const administratorRole = ref<boolean>(false)

try {
  administratorRole.value = sessionStorage.getItem(ADMIN_ROLE_KEY) === 'admin'
} catch {
  administratorRole.value = false
}

export function isAdministrator(): boolean {
  try {
    return sessionStorage.getItem(ADMIN_ROLE_KEY) === 'admin'
  } catch {
    return false
  }
}

export function setAdministrator(value: boolean): void {
  administratorRole.value = value
  try {
    if (value) sessionStorage.setItem(ADMIN_ROLE_KEY, 'admin')
    else sessionStorage.removeItem(ADMIN_ROLE_KEY)
  } catch {
    // Storage may be unavailable in restricted browser contexts.
  }
}

const pendingPath = ref<string | null>(null)
const accessRequested = ref(0)
let grantedPath: string | null = null

export function requestTechnicalDebugAccess(path: string): void {
  pendingPath.value = path
  accessRequested.value += 1
}

export function getTechnicalDebugAccessRequest(): { path: string | null; id: number } {
  return { path: pendingPath.value, id: accessRequested.value }
}

export function clearTechnicalDebugAccessRequest(): void {
  pendingPath.value = null
}

export function grantTechnicalDebugAccess(path: string): void {
  grantedPath = path
}

export function consumeTechnicalDebugAccess(path: string): boolean {
  if (grantedPath !== path) return false
  grantedPath = null
  return true
}
