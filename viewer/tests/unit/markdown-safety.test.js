// User intent: task text is written by people and by agents, so rendered markdown must be inert — no script, no remote
// load, no hostile link — while real markdown (headings, tables, code, links) still reads as a document.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

// The real vendored parser, evaluated against the jsdom window exactly as index.html's classic <script> does.
const dom = new JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only' });
dom.window.eval(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../vendor/marked.min.js'), 'utf8'));
globalThis.window = dom.window;
globalThis.document = dom.window.document

const { renderMarkdown, mountMarkdown } = await import('../../js/components/markdown.js');

// Renders the way every caller does: the string goes through innerHTML, then the live tree is inspected.
function render(src) {
  const host = document.createElement('div');
  host.innerHTML = renderMarkdown(src);
  return host;
}

const SAFE_URL = /^(https?:|mailto:|#|\/|\.|[^:]*$)/i;
function assertInert(host, src) {
  const all = [...host.querySelectorAll('*')];
  for (const tag of ['script', 'iframe', 'img', 'style', 'svg', 'math', 'object', 'embed', 'form', 'input', 'link', 'meta', 'base', 'video', 'audio', 'template', 'noscript']) {
    assert.equal(host.querySelector(tag), null, `<${tag}> survived: ${src}\n → ${host.innerHTML}`);
  }
  for (const el of all) {
    assert.equal(el.namespaceURI, 'http://www.w3.org/1999/xhtml', `foreign element <${el.localName}>: ${src}`);
    for (const attr of el.attributes) {
      assert.ok(!/^on/i.test(attr.name), `event handler ${attr.name}: ${src}\n → ${host.innerHTML}`);
      assert.notEqual(attr.name, 'style', `style attribute: ${src}`);
      assert.ok(!['src', 'srcset', 'xlink:href', 'formaction', 'action', 'srcdoc', 'id', 'name'].includes(attr.name), `${attr.name} attribute: ${src}`);
      assert.ok(!/(javascript|data|vbscript)\s*:/i.test(attr.value.replace(/[\u0000-\u0020]/g, '')), `hostile URL in ${attr.name}: ${src}\n → ${host.innerHTML}`);
    }
    if (el.hasAttribute('href')) assert.match(el.getAttribute('href').trim(), SAFE_URL, `href scheme: ${src}\n → ${host.innerHTML}`);
  }
  // No comment survives either: nothing may hide between the sanitiser's parse and the caller's.
  const walker = document.createTreeWalker(host, dom.window.NodeFilter.SHOW_COMMENT);
  assert.equal(walker.nextNode(), null, `comment survived: ${src}`);
}

const HOSTILE = [
  // The brief's six.
  '<img src=x onerror=alert(1)>',
  '[a](javascript:alert(1))',
  '<script>alert(1)</script>',
  '<a href="data:text/html,x">d</a>',
  '<iframe src=x>',
  '<p style="color:red" onclick="x()">t</p>',
  // Scheme disguised by case, by whitespace and control characters, and by entities.
  '<a href="JaVaScRiPt:alert(1)">x</a>',
  '<a href="java\tscript:alert(1)">x</a>',
  '<a href="java\nscript:alert(1)">x</a>',
  '<a href=" \u0001javascript:alert(1)">x</a>',
  '<a href="&#106;avascript:alert(1)">x</a>',
  '<a href="&#x6A;&#x61;&#x76;&#x61;&#x73;&#x63;&#x72;&#x69;&#x70;&#x74;&#x3A;alert(1)">x</a>',
  '<a href="javascript&colon;alert(1)">x</a>',
  '<a href="java&Tab;script:alert(1)">x</a>',
  '[a](JAVASCRIPT:alert(1))',
  '[a](<java\tscript:alert(1)>)',
  '[a](vbscript:msgbox(1))',
  '[a](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)',
  '<a href="file:///etc/passwd">x</a>',
  '<a href="blob:https://x/1">x</a>',
  // Foreign content and the parser tricks that ride on it.
  '<svg onload=alert(1)><script>alert(1)</script></svg>',
  '<svg><a xlink:href="javascript:alert(1)"><text>x</text></a></svg>',
  '<a xlink:href="javascript:alert(1)">x</a>',
  '<math><mtext><table><mglyph><style><!--</style><img title="--&gt;&lt;img src=1 onerror=alert(1)&gt;">',
  '<math><maction actiontype="statusline#http://x" xlink:href="javascript:alert(1)">x</maction></math>',
  '<svg><style><img src=x onerror=alert(1)></style></svg>',
  '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
  '<template><img src=x onerror=alert(1)></template>',
  '<textarea><img src=x onerror=alert(1)></textarea>',
  '<title><img src=x onerror=alert(1)></title>',
  '<style>@import "http://x/y.css";</style>',
  '<!-- <img src=x onerror=alert(1)> -->',
  '<!--><img src=x onerror=alert(1)>-->',
  // Markdown images, remote loads and page-level elements.
  '![alt](http://evil.example/pixel.png)',
  '![alt](x "t" onerror=alert(1))',
  '<video src=x onerror=alert(1)></video><audio src=x></audio>',
  '<object data="javascript:alert(1)"></object><embed src=x>',
  '<link rel=stylesheet href="http://x/y.css"><meta http-equiv=refresh content="0;url=http://x"><base href="http://x/">',
  '<form action="javascript:alert(1)"><input name=q onfocus=alert(1) autofocus><button formaction="javascript:alert(1)">b</button></form>',
  '<details open ontoggle=alert(1)><summary>s</summary>x</details>',
  '<div id="sidebar" name="store" class="modal">clobber</div>',
  '<a href="https://x" onclick="alert(1)" target="_self" style="position:fixed">x</a>',
  // Raw HTML tables (review focus): the table may stay, the handlers may not.
  '<table onclick="alert(1)"><tr><td background="javascript:alert(1)" style="x" onmouseover="alert(1)">c</td></tr></table>',
];

for (const src of HOSTILE) {
  test(`hostile input renders inert: ${JSON.stringify(src).slice(0, 90)}`, () => {
    assertInert(render(src), src);
    assertInert(render(`intro\n\n${src}\n\n- item ${src}\n\n> quote ${src}`), `wrapped: ${src}`);
  });
}

test('a script, style or foreign subtree is dropped with its content, not shown as text', () => {
  assert.doesNotMatch(render('<script>alert(1)</script>').textContent, /alert/);
  assert.doesNotMatch(render('<style>p { display: none }</style>').textContent, /display/);
  assert.doesNotMatch(render('a<svg><text>inside</text></svg>b').textContent, /inside/);
  assert.equal(render('before <iframe src=x>fallback</iframe> after').querySelector('iframe'), null);
});

test('an element outside the allow-list is unwrapped: its text stays, the element and its attributes go', () => {
  const host = render('<div class="modal" onclick="x()">kept <span data-x="1">text</span></div>');
  assert.equal(host.querySelector('div'), null);
  assert.match(host.textContent, /kept text/);
  const span = host.querySelector('span');
  assert.equal(span.attributes.length, 0);
});

test('images are removed entirely: task text never triggers a remote load', () => {
  const host = render('before ![chart](https://example.com/a.png) after\n\n<img src="https://example.com/b.png" alt="b">');
  assert.equal(host.querySelector('img'), null);
  assert.doesNotMatch(host.innerHTML, /example\.com/);
  assert.match(host.textContent, /before\s+after/);
});

test('a GFM pipe table and a ## heading render as a table and an h2', () => {
  const host = render('## Plan\n\n| Step | Owner |\n|:-----|------:|\n| one  | a     |\n| two  | b     |\n');
  assert.equal(host.querySelector('h2').textContent, 'Plan');
  const table = host.querySelector('table');
  assert.ok(table, host.innerHTML);
  assert.deepEqual([...table.querySelectorAll('th')].map((th) => th.textContent), ['Step', 'Owner']);
  assert.equal(table.querySelectorAll('tbody tr').length, 2);
  assert.equal(table.querySelectorAll('td')[1].textContent, 'a');
  // Column alignment is the one presentational attribute a table keeps.
  assert.equal(table.querySelectorAll('th')[1].getAttribute('align'), 'right');
  assert.doesNotMatch(host.textContent, /##|\|/);
});

test('ordinary markdown survives: emphasis, lists, code, fenced code with a language, quotes, rules, strikethrough', () => {
  const host = render('**bold** *em* ~~gone~~ `code`\n\n- a\n- b\n\n1. one\n2. two\n\n```js\nconst x = 1 < 2;\n```\n\n> quoted\n\n---\n');
  for (const tag of ['strong', 'em', 'del', 'code', 'ul', 'ol', 'li', 'pre', 'blockquote', 'hr']) {
    assert.ok(host.querySelector(tag), `<${tag}> missing: ${host.innerHTML}`);
  }
  const fenced = host.querySelector('pre code');
  assert.equal(fenced.className, 'language-js');
  assert.equal(fenced.textContent.trim(), 'const x = 1 < 2;');
});

test('a class is kept only on code and only as a language name', () => {
  assert.equal(render('<code class="language-python">x</code>').querySelector('code').className, 'language-python');
  assert.equal(render('<code class="btn btn--critical">x</code>').querySelector('code').hasAttribute('class'), false);
  assert.equal(render('<p class="modal">x</p>').querySelector('p').hasAttribute('class'), false);
  assert.equal(render('<span class="language-js">x</span>').querySelector('span').hasAttribute('class'), false);
});

test('an https link gets rel and target; so does http; the author cannot choose either', () => {
  for (const src of ['[site](https://example.com/a?b=1#c)', '<a href="http://example.com" target="_self" rel="opener">site</a>', '<https://example.com>']) {
    const a = render(src).querySelector('a');
    assert.ok(a, src);
    assert.match(a.getAttribute('href'), /^https?:\/\/example\.com/);
    assert.equal(a.getAttribute('rel'), 'noopener noreferrer', src);
    assert.equal(a.getAttribute('target'), '_blank', src);
  }
});

test('relative, fragment and mailto links keep their href and are not sent to a new tab', () => {
  for (const [src, href] of [
    ['[t](#/task/abc-001)', '#/task/abc-001'], ['[d](docs/plan.md)', 'docs/plan.md'], ['[r](/api/x)', '/api/x'],
    ['[u](../up.md)', '../up.md'], ['[q](?a=1)', '?a=1'], ['[m](mailto:a@example.com)', 'mailto:a@example.com'],
  ]) {
    const a = render(src).querySelector('a');
    assert.equal(a.getAttribute('href'), href, src);
    assert.equal(a.hasAttribute('target'), false, src);
    assert.equal(a.hasAttribute('rel'), false, src);
  }
  // A protocol-relative link leaves the site, so it is treated like an absolute one.
  const pr = render('<a href="//example.com/x">x</a>').querySelector('a');
  assert.equal(pr.getAttribute('target'), '_blank');
  assert.equal(pr.getAttribute('rel'), 'noopener noreferrer');
});

test('a link whose scheme is refused keeps its text but loses the href', () => {
  const a = render('[click](javascript:alert(1))').querySelector('a');
  if (a) assert.equal(a.hasAttribute('href'), false);
  assert.match(render('[click](javascript:alert(1))').textContent, /click/);
  const raw = render('<a href="data:text/html,x" title="t">d</a>').querySelector('a');
  assert.equal(raw.hasAttribute('href'), false);
  assert.equal(raw.getAttribute('title'), 't');
  assert.equal(raw.hasAttribute('target'), false);
});

test('sanitising is stable: the output parsed again and sanitised again is unchanged', () => {
  for (const src of [...HOSTILE, '## h\n\n| a |\n|---|\n| b |\n\n[x](https://example.com)']) {
    const once = renderMarkdown(src);
    const host = document.createElement('div');
    host.innerHTML = once;
    assert.equal(host.innerHTML, once, `re-parse changed the markup: ${src}`);
  }
});

test('empty input renders nothing; non-string input is rendered as its text, never thrown on', () => {
  for (const empty of ['', null, undefined]) assert.equal(renderMarkdown(empty), '');
  assert.match(render(42).textContent, /42/);
  assert.doesNotThrow(() => renderMarkdown({ a: 1 }));
  assert.doesNotThrow(() => renderMarkdown(['<img src=x onerror=alert(1)>']));
  assertInert(render(['<img src=x onerror=alert(1)>']), 'array input');
});

test('mountMarkdown assigns the sanitised markup', () => {
  const el = document.createElement('div');
  mountMarkdown(el, '# T\n\n<img src=x onerror=alert(1)>');
  assert.equal(el.querySelector('h1').textContent, 'T');
  assertInert(el, 'mountMarkdown');
});

test('without the parser the text is shown escaped, never interpreted', () => {
  const marked = dom.window.marked;
  delete dom.window.marked;
  try {
    const host = render('<img src=x onerror=alert(1)> **b**');
    assert.equal(host.querySelector('img'), null);
    assert.equal(host.querySelector('pre.md-fallback').textContent, '<img src=x onerror=alert(1)> **b**');
  } finally { dom.window.marked = marked; }
});

// ── Task lists: plans are checkbox step lists, so a rendered plan must still say which steps are done. ──
const taskState = (li) => {
  const mark = [...li.children].find((c) => c.classList.contains('md-task')) ?? li.querySelector(':scope > p > .md-task');
  return mark ? [...mark.classList].find((c) => c.startsWith('md-task--')) : null;
};

test('a task list keeps its checked state as an inert mark: no input survives, and the two states differ', () => {
  const host = render('- [x] done step\n- [ ] open step');
  assert.equal(host.querySelector('input'), null);
  const [done, open] = host.querySelectorAll('li');
  assert.notEqual(done.innerHTML.replace('done step', ''), open.innerHTML.replace('open step', ''));
  assert.equal(taskState(done), 'md-task--done');
  assert.equal(taskState(open), 'md-task--open');
  // The state is announced as text, not carried by the drawn box alone.
  assert.match(done.textContent, /^\s*done:\s+done step/);
  assert.match(open.textContent, /^\s*to do:\s+open step/);
  // Nothing clickable or focusable is created.
  assert.equal(host.querySelector('button, a, [tabindex], [onclick], [role]'), null);
  assertInert(host, 'task list');
});

test('a nested task list with mixed states keeps each item its own state', () => {
  const host = render('- [x] parent done\n  - [ ] child open\n  - [x] child done\n- [ ] parent open\n  - plain child\n\n1. [x] numbered done\n2. [ ] numbered open');
  const states = Object.fromEntries([...host.querySelectorAll('li')].map((li) => [li.firstChild.parentElement.childNodes.length && li.textContent.replace(/^(\s*(done|to do):)?\s*/, '').split('\n')[0].trim(), taskState(li)]));
  assert.deepEqual(states, {
    'parent done': 'md-task--done', 'child open': 'md-task--open', 'child done': 'md-task--done',
    'parent open': 'md-task--open', 'plain child': null, 'numbered done': 'md-task--done', 'numbered open': 'md-task--open',
  });
  assert.equal(host.querySelector('input'), null);
});

test('an author cannot write the task mark: its class is stripped, and only a checkbox becomes one', () => {
  for (const src of [
    '<span class="md-task md-task--done">x</span> not done',
    '- <span class="md-task md-task--done"><span class="md-task__label">done: </span></span> spoofed',
    '<input type="text" value="x"> <input type="radio" checked> <input type="checkbox" checked onclick="alert(1)" id="c" name="n">',
  ]) {
    const host = render(src);
    assert.equal(host.querySelector('input'), null, src);
    assertInert(host, src);
    for (const el of host.querySelectorAll('span')) {
      if (el.closest('.md-task')) continue;
      assert.equal(el.hasAttribute('class'), false, src);
    }
  }
  assert.equal(render('<span class="md-task md-task--done">x</span> not done').querySelector('.md-task'), null);
  assert.equal(render('- <span class="md-task md-task--done">x</span> spoofed').querySelector('.md-task'), null);
  // A raw checkbox is the same inert mark, with none of its own attributes.
  const marks = render('<input type="text" value="x"> <input type="checkbox" checked onclick="alert(1)" id="c">').querySelectorAll('.md-task');
  assert.equal(marks.length, 1);
  assert.equal(marks[0].className, 'md-task md-task--done');
  assert.equal(marks[0].attributes.length, 1);
});

test('a slash, control characters, then a slash is still a link that leaves the site', () => {
  for (const src of ['<a href="/\t/evil.example">x</a>', '<a href="/\n/evil.example">x</a>', '<a href="\\\t\\evil.example">x</a>', '<a href=" /\t/evil.example">x</a>']) {
    const a = render(src).querySelector('a');
    assert.ok(a.hasAttribute('href'), src);
    assert.equal(a.getAttribute('rel'), 'noopener noreferrer', JSON.stringify(src));
    assert.equal(a.getAttribute('target'), '_blank', JSON.stringify(src));
  }
});
