// User intent: the public viewer runs under a CSP with no inline style or script, so no served module may build inline styles, and every HTML sink is audited (a new sink fails here until someone checks its data source).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const JS = join(dirname(fileURLToPath(import.meta.url)), '../../js');
// The same rule as serve/viewer.py static_asset_allowed: no _dormant, dev, node_modules or tests, no *-demo.js.
const BARRED = new Set(['_dormant', 'dev', 'node_modules', 'tests']);

function served(dir = JS) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (BARRED.has(entry.name)) continue;
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...served(path));
    else if (entry.name.endsWith('.js') && !entry.name.endsWith('-demo.js')) out.push(path);
  }
  return out;
}

const files = served().map((path) => ({ rel: relative(JS, path).replaceAll('\\', '/'), lines: readFileSync(path, 'utf8').split('\n') }))

const code = (line) => !/^\s*(\/\/|\*|\/\*)/.test(line);

test('the walk found the served modules', () => {
  assert.ok(files.length > 100, `found ${files.length}`);
  assert.ok(files.some((f) => f.rel === 'util/h.js'));
  assert.ok(!files.some((f) => f.rel.startsWith('_dormant/') || f.rel.startsWith('dev/')));
});

test('no served module writes a style attribute (innerHTML markup or setAttribute)', () => {
  const found = [];
  for (const { rel, lines } of files) {
    lines.forEach((line, i) => {
      if (!code(line)) return;
      if (/\bstyle\s*=\s*["'\\]/.test(line) || /setAttribute\(\s*['"]style['"]/.test(line)) found.push(`${rel}:${i + 1}: ${line.trim()}`);
    });
  }
  assert.deepEqual(found, []);
});

// Every place a string becomes markup. The data source of each is why it is allowed.
const SINKS = [
  { file: 'components/card.js', pattern: /t\.innerHTML = html;/, source: '<template> parse of a constant, escaped card fragment' },
  { file: 'components/detail-page.js', pattern: /el\.innerHTML = renderMarkdown\(/, source: 'renderMarkdown: the sanitiser' },
  { file: 'components/edit/fields/md-field.js', pattern: /html: renderMarkdown\(/, source: 'renderMarkdown: the sanitiser' },
  { file: 'components/icon.js', pattern: /el\.innerHTML = inner;/, source: 'icon path markup from the constant icon table' },
  { file: 'components/markdown.js', pattern: /\.innerHTML = (renderMarkdown\(src\)|html);|return tpl\.innerHTML;/, source: 'the sanitiser itself (template parse and serialise)' },
  { file: 'components/right-rail.js', pattern: /quote\.innerHTML = renderMarkdown\(/, source: 'renderMarkdown: the sanitiser' },
  { file: 'components/sidebar.js', pattern: /(el|logo)\.innerHTML = /, source: 'clears the nav; the logo is a constant SVG' },
  { file: 'components/task-detail-document.js', pattern: /track\.innerHTML = html;/, source: 'html built from escaped fields in this file' },
  { file: 'screens/settings.js', pattern: /root\.innerHTML = '';/, source: 'clears a node' },
  { file: 'util/h.js', pattern: /el\.innerHTML = v;/, source: 'h({ html }) callers: md-field.js passes renderMarkdown output only' },
  { file: 'file-page.js', pattern: /content\.innerHTML = md\.render\(/, source: 'TaskmasterMarkdown.render: the sanitiser' },
  { file: 'lib/md-safe.js', pattern: /\.innerHTML/, source: 'the sanitiser itself' },
];
const SINK_LINE = /\.innerHTML\s*=(?!=)|insertAdjacentHTML\s*\(|\.outerHTML\s*=(?!=)|\bhtml:\s|document\.write\s*\(|\.srcdoc\s*=|return \w+\.innerHTML/;

test('every HTML sink is on the audited list', () => {
  const unlisted = [];
  for (const { rel, lines } of files) {
    lines.forEach((line, i) => {
      if (!code(line) || !SINK_LINE.test(line)) return;
      if (!SINKS.some((s) => s.file === rel && s.pattern.test(line))) unlisted.push(`${rel}:${i + 1}: ${line.trim()}`);
    });
  }
  assert.deepEqual(unlisted, [], 'a new HTML sink needs an audit: add it to SINKS with its data source');
});

test('the audited list holds no stale entry', () => {
  for (const sink of SINKS) {
    const file = files.find((f) => f.rel === sink.file);
    if (!file) continue;                                  // lib/md-safe.js arrives with the one-sanitizer step
    assert.ok(file.lines.some((l) => code(l) && sink.pattern.test(l)), `stale sink ${sink.file} ${sink.pattern}`);
  }
});
