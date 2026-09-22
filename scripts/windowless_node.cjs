'use strict';

// Test-runner preload: Playwright's installed launcher omits windowsHide on
// fixture/worker starts and owned-child cleanup. Do not modify node_modules or
// change its process ownership/cleanup semantics; only supply this default.
function install(childProcess, platform = process.platform) {
  if (platform !== 'win32') return;
  for (const name of ['spawn', 'spawnSync', 'fork']) {
    const original = childProcess[name];
    childProcess[name] = function (command, args, options) {
      if (Array.isArray(args) || options !== undefined) {
        return original.call(childProcess, command, args || [], {windowsHide: true, ...options});
      }
      return original.call(childProcess, command, {windowsHide: true, ...args});
    };
  }
}

install(require('node:child_process'));
require('node:module').syncBuiltinESMExports();
module.exports = {install};
