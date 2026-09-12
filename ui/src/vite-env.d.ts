/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** WebSocket URL of a Plexus runtime, e.g. ws://rig.local:8900/ws. */
  readonly VITE_PLEXUS_RUNTIME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
