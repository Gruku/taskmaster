// User intent: the exact words the plugin's stdio bridge (tm_bridge.py may_have_committed_text) answers a call with when the
// local server restarts or is cut, so the mod tests prove the band shows "connecting" and a write says it may have landed.
// tests/test_tm_bridge.py pins these two templates to the bridge's own text; change them together.
export const bridgeNotSent = (tool: string): string => `no connected MCP tool "${tool}" on a server named "tm": the local taskmaster server could not be reached; this call was not sent`
export const bridgeMayHaveCommitted = (tool: string): string => `no connected MCP tool "${tool}" on a server named "tm": the connection to the local taskmaster server was lost while this call ran; the call may have committed, check its effect before repeating it`
