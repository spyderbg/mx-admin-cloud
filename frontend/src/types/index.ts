// Mock data helper types only.
// Business code (cache, views, utils) must import from ./viewModels.

export interface User {
    name: string
    role: string
    avatarUri: string
}
