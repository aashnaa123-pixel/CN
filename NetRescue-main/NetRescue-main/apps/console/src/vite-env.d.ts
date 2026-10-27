/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the NetRescue API, e.g. http://127.0.0.1:8000 */
  readonly VITE_API_BASE_URL?: string
  /** Identity sent as X-NetRescue-Principal. */
  readonly VITE_PRINCIPAL?: string
  /** Role sent as X-NetRescue-Role: readonly | operator. */
  readonly VITE_ROLE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
