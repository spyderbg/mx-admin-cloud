/** What Google Identity Services hands the callback once the user signs in. */
export interface GoogleCredentialResponse {
  /** ID token for the signed-in Google account, verified by the backend. */
  credential: string
}

/** The part of the Google Identity Services `google.accounts.id` API the login page uses. */
export interface GoogleAccountsId {
  initialize(config: {
    client_id: string
    callback: (response: GoogleCredentialResponse) => void
    ux_mode?: 'popup' | 'redirect'
    context?: 'signin' | 'signup' | 'use'
  }): void
  renderButton(parent: HTMLElement, options: {
    type?: 'standard' | 'icon'
    theme?: 'outline' | 'filled_blue' | 'filled_black'
    size?: 'large' | 'medium' | 'small'
    text?: 'signin_with' | 'signup_with' | 'continue_with' | 'signin'
    shape?: 'rectangular' | 'pill' | 'circle' | 'square'
    logo_alignment?: 'left' | 'center'
    width?: number
  }): void
}

declare global {
  interface Window {
    google?: { accounts?: { id?: GoogleAccountsId } }
  }
}

const SCRIPT_SRC = 'https://accounts.google.com/gsi/client'
let loading: Promise<GoogleAccountsId> | null = null

/** Load the Google Identity Services script once and resolve its `google.accounts.id` API. */
export function loadGoogleIdentity(): Promise<GoogleAccountsId> {
  const ready = window.google?.accounts?.id
  if (ready) return Promise.resolve(ready)
  loading ??= new Promise<GoogleAccountsId>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = SCRIPT_SRC
    script.async = true
    script.onload = () => {
      const api = window.google?.accounts?.id
      if (api) resolve(api)
      else reject(new Error('Google sign-in is unavailable.'))
    }
    script.onerror = () => {
      // Let a later attempt (for example, after an ad blocker is turned off) try again.
      loading = null
      script.remove()
      reject(new Error('Could not load Google sign-in.'))
    }
    document.head.appendChild(script)
  })
  return loading
}
