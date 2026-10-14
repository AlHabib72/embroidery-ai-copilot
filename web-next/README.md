# web-next (typed client layer for a future Next.js studio)
`src/lib/schemas.ts` and `src/lib/ai-router.ts` are type-checked (`tsc --noEmit --strict`). They talk to the Python backend only; keys never reach the browser.
React components (PromptBox.tsx, StitchCanvas.tsx, ...) and the Next.js build are **PLANNED / NOT BUILT**: `react` types and Next.js are not installable offline in this environment,
so nothing here claims a passing `next build`. The working UI today is the dashboard served by `embcopilot web` (see GUI.md).
