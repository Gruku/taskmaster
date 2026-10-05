// User intent: throwaway probe — settle six unknowns (hex colour, theme, cross-plugin MCP, noun trees, session id on resume/clear, status coexistence) before the Taskmaster TUI spec.
import type { Register } from 'claude-code'

const findings: Record<string, string> = {}

async function withTimeout<T>($: any, work: Promise<T>, ms: number): Promise<T | 'timeout'> {
  return Promise.race([work, $.clock.sleep(ms).then(() => 'timeout' as const)])
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'probe', description: 'Report the mod-API probe findings' })
    $.ui.status('● probe-tm status line: is this beside your custom statusline?')

    findings.probeRr = `$.probeRr.version()=${await $.probeRr.version()}`
    const id = await $.session.id()
    findings['session.start'] = `source=${String((e as any).source)} id=${id}`
    const history = ((await $.store.get('starts')) as unknown[] | undefined) ?? []
    await $.store.set('starts', [...history, { id, source: (e as any).source, cwd: e.cwd }].slice(-10))

    const theme = (await $.config.list()).find(row => row.key === 'theme')
    findings.theme = JSON.stringify(theme?.value)
    findings.COLORTERM = String(await $.env.get('COLORTERM'))
    findings.TERM_PROGRAM = String(await $.env.get('TERM_PROGRAM'))
    findings.WT_SESSION = (await $.env.get('WT_SESSION')) ? 'set' : 'unset'

    void (async () => {
      for (const server of ['plugin:taskmaster:tm', 'plugin_taskmaster_tm']) {
        try {
          const ran = await withTimeout($, $.mcp.call(server, 'backlog_handover_list', {}), 5000)
          findings[`mcp.call ${server}`] =
            ran === 'timeout'
              ? 'timeout after 5s'
              : `isError=${ran.isError} structured=${ran.structuredContent !== undefined} text=${JSON.stringify(ran.content).slice(0, 400)}`
        } catch (err) {
          findings[`mcp.call ${server}`] = `threw: ${String(err).slice(0, 200)}`
        }
      }
    })()

    return next(e)
  })

  on('classic.SessionStart', async ($, e, next) => {
    const seen = ((await $.store.get('classicStarts')) as unknown[] | undefined) ?? []
    const raw = e as any
    await $.store.set('classicStarts', [...seen, { source: raw.source, session_id: raw.session_id, id: await $.session.id() }].slice(-10))
    return next(e)
  })

  on('session.end', async ($, e, next) => {
    const ends = ((await $.store.get('ends')) as unknown[] | undefined) ?? []
    await $.store.set('ends', [...ends, { reason: e.reason, sessionId: e.sessionId }].slice(-10))
    return next(e)
  })

  on('command.run', { command: 'probe' }, async $ => {
    const starts = await $.store.get('starts')
    const ends = await $.store.get('ends')
    const classicStarts = await $.store.get('classicStarts')
    const lines = Object.entries(findings).map(([k, v]) => `${k}: ${v}`)
    return { text: [...lines, `store.starts: ${JSON.stringify(starts)}`, `store.ends: ${JSON.stringify(ends)}`, `store.classicStarts: ${JSON.stringify(classicStarts)}`].join('\n') }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const ui = $.ui.resolve(e)
    const { Box, Text } = ui
    let kitRow: unknown
    try {
      kitRow = await $.probeRr.swatch({ label: 'noun tree via $.probeRr', fg: '#5e79e6', bg: '#262523' })
    } catch (err) {
      kitRow = <Text>noun tree FAILED: {String(err)}</Text>
    }
    return (
      <Box flexDirection="column">
        <Box>
          <Text color="#5e79e6">hex #5e79e6 </Text>
          <Text color="rgb(94,121,230)">rgb() </Text>
          <Text color="permission">themekey:permission </Text>
          <Text color="blue">ansi:blue </Text>
          <Text color="#f5f3ed" backgroundColor="#262523"> hex bg #262523 </Text>
        </Box>
        <Box>
          <Box borderStyle="single" borderColor="#7a756c" paddingX={1}><Text>single</Text></Box>
          <Box borderStyle="round" borderColor="#5e79e6" paddingX={1}><Text>round</Text></Box>
          <Box borderStyle="bold" borderColor="#d9a441" paddingX={1}><Text>bold</Text></Box>
          <Box backgroundColor="#3f58c0" paddingX={2} width={24}><Text>box bg #3f58c0</Text></Box>
        </Box>
        {kitRow as any}
      </Box>
    )
  })
}
