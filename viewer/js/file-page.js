// User intent: the sandboxed /file/ markdown page renders its file through the one shared sanitiser and keeps its zoom working even though a sandbox's opaque origin makes localStorage throw.
// Classic script (a sandboxed page can load only classic scripts from the viewer, never module scripts): needs TaskmasterMarkdown from lib/md-safe.js.
(function () {
  var ZOOM_KEY = 'taskmaster-docs-zoom';
  var STEP = 10, MIN = 60, MAX = 200, BASE = 14;
  var content = document.getElementById('content');
  var label = document.getElementById('zoom-label');

  function decode(b64) {
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder('utf-8').decode(bytes);
  }

  function show(text) {
    var md = globalThis.TaskmasterMarkdown;
    if (md && globalThis.marked) {
      content.innerHTML = md.render(text);
      // The page's CSP sandbox has no allow-popups, so a target=_blank link would do nothing: links open in place.
      var links = content.querySelectorAll('a[target]');
      for (var i = 0; i < links.length; i += 1) links[i].removeAttribute('target');
    } else {
      var pre = document.createElement('pre');
      pre.className = 'md-fallback';
      pre.textContent = text;
      content.replaceChildren(pre);
    }
  }

  var text = '';
  try { text = decode(content.getAttribute('data-b64') || ''); } catch (e) { text = ''; }
  show(text);

  // Storage is read once and written on change; where it throws (the sandbox), the value lives in memory.
  var zoom = 100;
  try {
    var saved = parseInt(localStorage.getItem(ZOOM_KEY) || '100', 10);
    if (isFinite(saved)) zoom = saved;
  } catch (e) { /* no storage here */ }

  function apply(persist) {
    zoom = Math.max(MIN, Math.min(MAX, zoom));
    content.style.fontSize = (BASE * zoom / 100) + 'px';
    label.textContent = zoom + '%';
    if (persist) {
      try { localStorage.setItem(ZOOM_KEY, String(zoom)); } catch (e) { /* kept in memory */ }
    }
  }

  document.getElementById('zoom-in').addEventListener('click', function () { zoom += STEP; apply(true); });
  document.getElementById('zoom-out').addEventListener('click', function () { zoom -= STEP; apply(true); });
  document.getElementById('zoom-reset').addEventListener('click', function () { zoom = 100; apply(true); });
  apply(false);
})();
