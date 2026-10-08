// User intent: the mocked UI specs must not fail because the file server fell over — every parallel browser gets its
// files at once, over kept-alive connections, from a server that never refuses a connection under a burst.
// Usage: node static-server.mjs <port> [root]   (read-only, 127.0.0.1 only)
import http from 'node:http';
import { createReadStream, statSync } from 'node:fs';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const port = Number(process.argv[2]);
const root = resolve(process.argv[3] ?? join(dirname(fileURLToPath(import.meta.url)), '..', '..'));
if (!Number.isInteger(port) || port <= 0) throw new Error('usage: static-server.mjs <port> [root]');

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon', '.png': 'image/png', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf',
  '.md': 'text/markdown; charset=utf-8', '.txt': 'text/plain; charset=utf-8',
};

function fileFor(url) {
  let path;
  try { path = decodeURIComponent(new URL(url, 'http://x').pathname); } catch { return null; }
  if (path.includes('\0')) return null;
  let file = resolve(join(root, path));
  if (file !== root && !file.startsWith(root + sep)) return null;
  try {
    if (statSync(file).isDirectory()) file = join(file, 'index.html');
    return { file, size: statSync(file).size };
  } catch { return null; }
}

const server = http.createServer((req, res) => {
  if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405, { Allow: 'GET, HEAD' }).end(); return; }
  const hit = fileFor(req.url);
  if (!hit) { res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }).end('not found'); return; }
  res.writeHead(200, {
    'Content-Type': TYPES[extname(hit.file).toLowerCase()] ?? 'application/octet-stream',
    'Content-Length': hit.size,
    'Cache-Control': 'no-store',
  });
  if (req.method === 'HEAD') { res.end(); return; }
  createReadStream(hit.file).on('error', () => res.destroy()).pipe(res);
});
server.listen(port, '127.0.0.1');
