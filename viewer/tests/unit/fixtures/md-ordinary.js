// User intent: ordinary markdown that must render identically through the app's module face and the classic md-safe.js the sandboxed file page loads.
export const ORDINARY = [
  '# Title\n\nA paragraph with **bold**, _em_ and `code`.',
  '- [x] done\n- [ ] todo\n\n1. one\n2. two',
  '| a | b |\n|---|---|\n| 1 | 2 |',
  '[rel](docs/a.md) [abs](https://example.com) [frag](#x)',
  '```js\nconst x = 1 < 2;\n```',
  '',
];
