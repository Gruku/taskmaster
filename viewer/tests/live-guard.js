// User intent: the live-server specs write viewer prefs to whatever viewer they reach (this once overwrote
// the user's real prefs), so they run only when someone has explicitly said this viewer may be written to.
export function requireLiveOptIn(env = process.env) {
  if (env.TM_LIVE_SPECS_OK === '1') return;
  throw new Error(
    'Refusing to run the live-server viewer specs: they write viewer prefs (and backlog entities) to the '
    + 'viewer they reach, by default http://127.0.0.1:8765. Set TM_LIVE_SPECS_OK=1 only when that viewer '
    + 'serves a throwaway backlog. The mocked specs need no server: npm run test:mock.',
  );
}
