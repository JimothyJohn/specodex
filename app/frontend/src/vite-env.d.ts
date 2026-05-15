/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string;
  // 'v1' (Express, default) | 'v2' (Python FastAPI). Phase 1.4 of
  // todo/PYTHON_BACKEND.md. Unset is treated as 'v1'.
  readonly VITE_API_VERSION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
