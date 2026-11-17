// User intent: apply the saved theme before first paint, from a classic external file so the page needs no inline script under its CSP.
// Duplicates resolveTheme() in js/lib/theme.js (key 'tm.theme') — keep the two in agreement.
(function () {
  var p = 'dark';
  try { p = localStorage.getItem('tm.theme') || 'dark'; } catch (e) {}
  var dark = true;
  try { dark = matchMedia('(prefers-color-scheme: dark)').matches; } catch (e) {}
  document.documentElement.dataset.theme = p === 'system' ? (dark ? 'dark' : 'light') : (p === 'light' ? 'light' : 'dark');
})();
