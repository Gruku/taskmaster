'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {install} = require('../scripts/windowless_node.cjs');

test('Windows helper defaults hide every supported overload without spawning', () => {
  for (const name of ['spawn', 'spawnSync', 'fork']) {
    const calls = [];
    const fake = Object.fromEntries(['spawn', 'spawnSync', 'fork'].map(key => [key, (...args) => {
      calls.push(args);
      return 'owned-result';
    }]));
    install(fake, 'win32');
    assert.equal(fake[name]('helper', ['arg'], {cwd: 'scoped'}), 'owned-result');
    assert.deepEqual(calls.pop(), ['helper', ['arg'], {windowsHide: true, cwd: 'scoped'}]);
    fake[name]('helper', {shell: true});
    assert.deepEqual(calls.pop(), ['helper', {windowsHide: true, shell: true}]);
    fake[name]('helper', undefined, {env: {TEST: 'yes'}});
    assert.deepEqual(calls.pop(), ['helper', [], {windowsHide: true, env: {TEST: 'yes'}}]);
    fake[name]('helper', [], {windowsHide: false});
    assert.equal(calls.pop()[2].windowsHide, false);
  }
});

test('non-Windows helpers remain unchanged', () => {
  const fake = {spawn() {}, spawnSync() {}, fork() {}};
  const originals = {...fake};
  install(fake, 'linux');
  assert.deepEqual(fake, originals);
});
