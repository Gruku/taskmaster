// User intent: contract for the throwaway probe noun — tests whether a planned $.rr can return finished element trees as data.
export type ProbeRr = {
  version: () => string
  swatch: (props: { label: string; fg: string; bg?: string }) => unknown
}

declare module 'claude-code' {
  interface EngineInterface {
    probeRr: ProbeRr
  }
}
