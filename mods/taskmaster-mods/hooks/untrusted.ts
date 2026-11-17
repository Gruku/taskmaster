// User intent: the mods show and judge team-server replies as plain text, so they remove the untrusted-data frames the server
// puts around teammates' text (a line-for-line port of taskmaster/untrusted.py; both are tested on one fixture).
const J = '⁠'
const ESCAPED = /(<|&lt;|&#0*60;|&#x0*3c;)(\s*\/?\s*)(⁠+)(untrusted[-_]data)/giu
const NOTE_ESCAPED = /(?<![^\n])⁠(⁠*\[taskmaster:)/g
const FRAME = /<untrusted-data-([0-9a-f]{8}) authors="([^"\n]*)">\n([\s\S]*?)\n<\/untrusted-data-\1>/g

/** Reverses the server's escaping of a tag opener or note line inside framed content; removes exactly one joiner. */
export function unescapeContent(text: string): string {
  return text.replace(NOTE_ESCAPED, '$1').replace(ESCAPED, (_m, open: string, gap: string, joiners: string, name: string) => open + gap + joiners.slice(J.length) + name)
}

/** Every well-formed frame replaced by its unescaped content, minus the note line; other text untouched. */
export function unframe(text: string): string {
  if (typeof text !== 'string' || !text.includes('<untrusted-data-')) return text
  return text.replace(FRAME, (_m, _id: string, _authors: string, body: string) => {
    const at = body.indexOf('\n')
    return unescapeContent(at === -1 ? '' : body.slice(at + 1))
  })
}
