// User intent: the shared modal shell must hold in a real browser what jsdom cannot show — Tab stays inside the
// topmost dialog, the page behind is out of reach, a phone gets a full-height sheet, and nothing casts a shadow.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';

// A write the mock did not expect means the page talked to an endpoint this spec never set up.
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

// There is no harness page: the shell is driven through its module on a booted screen.
async function boot(page, prefs = {}) {
  await mockApi(page, { '/api/viewer/prefs': { theme: 'dark', ui: {}, screens: {}, ...prefs } });
  await page.goto('/#/settings');
  await expect(page.locator('#sidebar .sidebar-link').first()).toBeVisible();
  await page.evaluate(() => import('/js/components/modal.js').then((m) => { window.__m = m; window.__h = {}; }));
}

// Opens a modal named `name` (kept on window.__h) with `inputs` text fields in the body and Cancel/Save in the footer.
async function open(page, name, { inputs = 2, veto = false, ...opts } = {}) {
  await page.evaluate(({ name, inputs, veto, opts }) => {
    const modal = window.__m.openModal({ title: `Modal ${name}`, ...opts, ...(veto ? { onRequestClose: () => false } : {}) });
    for (let i = 0; i < inputs; i++) {
      const input = document.createElement('input');
      input.id = `${name}-in-${i}`;
      modal.body.appendChild(input);
    }
    for (const label of ['Cancel', 'Save']) {
      const button = document.createElement('button');
      button.type = 'button';
      button.id = `${name}-${label.toLowerCase()}`;
      button.textContent = label;
      modal.footer.appendChild(button);
    }
    window.__h[name] = modal;
  }, { name, inputs, veto, opts });
  await expect(dialog(page, name)).toBeVisible();
}

const dialog = (page, name) => page.getByRole('dialog', { name: `Modal ${name}` });
const activeId = (page) => page.evaluate(() => document.activeElement?.id || document.activeElement?.classList[0] || document.activeElement?.tagName);
const count = (page) => page.evaluate(() => window.__m.openModalCount());

test('Tab and Shift+Tab stay inside the dialog and wrap at both ends', async ({ page }) => {
  await boot(page);
  await open(page, 'a');
  await expect.poll(() => activeId(page)).toBe('a-in-0');   // first focusable of the body
  // Tab order: close button (header), two inputs, Cancel, Save.
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('a-save');
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('modal-close');           // wrapped forward
  await page.keyboard.press('Shift+Tab');
  expect(await activeId(page)).toBe('a-save');                // wrapped backward
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => window.__h.a.dialog.contains(document.activeElement))).toBe(true);
  }
});

test('Tab pulls focus back into the dialog when it was lost to the page', async ({ page }) => {
  await boot(page);
  await open(page, 'a');
  await page.evaluate(() => document.activeElement.blur());
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('modal-close');
  await page.evaluate(() => document.activeElement.blur());
  await page.keyboard.press('Shift+Tab');
  expect(await activeId(page)).toBe('a-save');
});

test('a dialog with nothing but its close button keeps Tab on it', async ({ page }) => {
  await boot(page);
  await page.evaluate(() => { window.__h.a = window.__m.openModal({ title: 'Modal a' }); });
  await expect.poll(() => activeId(page)).toBe('modal-close');
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('modal-close');
  await page.keyboard.press('Shift+Tab');
  expect(await activeId(page)).toBe('modal-close');
});

// The shell's own Tab wrap must agree with the browser's sequence at the edges, or Tab skips or sticks there.
test('a control of no size is still in the Tab cycle, as the browser has it', async ({ page }) => {
  await boot(page);
  await page.evaluate(() => {
    const modal = window.__m.openModal({ title: 'Modal z' });
    const input = document.createElement('input');
    input.id = 'z-in';
    const zero = document.createElement('button');
    zero.type = 'button';
    zero.id = 'z';
    zero.setAttribute('aria-label', 'Zero');
    zero.style.cssText = 'width:0;height:0;padding:0;border:0';
    modal.body.append(input, zero);
    window.__h.z = modal;
  });
  await expect.poll(() => activeId(page)).toBe('z-in');
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('z');
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('modal-close');           // wrapped to the first control
  await page.keyboard.press('Shift+Tab');
  expect(await activeId(page)).toBe('z');
});

test('what a closed details holds is out of the Tab cycle until it is opened', async ({ page }) => {
  await boot(page);
  await page.evaluate(() => {
    const modal = window.__m.openModal({ title: 'Modal d' });
    const input = document.createElement('input');
    input.id = 'd-in';
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.id = 'd-sum';
    summary.textContent = 'More';
    const inner = document.createElement('input');
    inner.id = 'd-inner';
    details.append(summary, inner);
    modal.body.append(input, details);
    window.__h.d = modal;
  });
  await expect.poll(() => activeId(page)).toBe('d-in');
  const seen = [];
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press('Tab');
    seen.push(await activeId(page));
  }
  expect(seen).toEqual(['d-sum', 'modal-close', 'd-in', 'd-sum', 'modal-close', 'd-in']);
  await page.evaluate(() => { document.querySelector('details').open = true; });
  await page.locator('#d-sum').focus();
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('d-inner');
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('modal-close');
  await page.keyboard.press('Shift+Tab');
  expect(await activeId(page)).toBe('d-inner');
});

test('with two modals open, Tab stays in the top one and Escape closes only the top one', async ({ page }) => {
  await boot(page);
  await open(page, 'a');
  await page.locator('#a-save').focus();
  await open(page, 'b', { size: 'sm' });
  await expect.poll(() => activeId(page)).toBe('b-in-0');
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => window.__h.b.dialog.contains(document.activeElement))).toBe(true);
  }
  expect(await page.evaluate(() => [window.__h.a.isTop(), window.__h.b.isTop()])).toEqual([false, true]);
  await page.keyboard.press('Escape');
  await expect(dialog(page, 'b')).toHaveCount(0);
  await expect(dialog(page, 'a')).toBeVisible();
  expect(await activeId(page)).toBe('a-save');                // back on the control that opened it
  await page.keyboard.press('Escape');
  expect(await count(page)).toBe(0);
});

test('focus returns to the opener; when the opener is gone it lands in the screen, never on body', async ({ page }) => {
  await boot(page);
  const toggle = page.locator('#theme-toggle');
  await toggle.focus();
  await open(page, 'a');
  await page.keyboard.press('Escape');
  await expect(toggle).toBeFocused();

  // The board re-renders under an open modal: the control that opened it no longer exists.
  await page.evaluate(() => {
    const gone = document.createElement('button');
    gone.id = 'gone';
    document.getElementById('topbar-primary').appendChild(gone);
    gone.focus();
  });
  await open(page, 'b');
  await page.evaluate(() => document.getElementById('gone').remove());
  await page.keyboard.press('Escape');
  expect(await count(page)).toBe(0);
  expect(await page.evaluate(() => document.activeElement !== document.body
    && document.getElementById('screen-mount').contains(document.activeElement))).toBe(true);
});

test('a modal opened from a popover item that went with the popover hands focus back to the button that opened the popover', async ({ page }) => {
  await boot(page);
  // Like a control parked behind Filters: the item exists only while its popover is open.
  await page.evaluate(() => import('/js/components/popover.js').then(({ openPopover }) => {
    const anchor = document.createElement('button');
    anchor.id = 'menu-button';
    anchor.textContent = 'Filters';
    document.getElementById('topbar-primary').appendChild(anchor);
    const item = document.createElement('button');
    item.id = 'menu-item';
    item.textContent = 'Add task';
    openPopover({ anchor, content: item, label: 'Filters' });
  }));
  await expect(page.locator('#menu-item')).toBeFocused();
  await open(page, 'a');
  // Focus moved into the dialog, so the popover closed and took its item with it.
  await expect(page.locator('#menu-item')).toHaveCount(0);
  await page.keyboard.press('Escape');
  expect(await count(page)).toBe(0);
  await expect(page.locator('#menu-button')).toBeFocused();
});

test('a click on the overlay closes; a drag from inside the dialog to the overlay does not', async ({ page }) => {
  await boot(page);
  await open(page, 'a');
  const box = await dialog(page, 'a').boundingBox();
  // Press inside the dialog, release on the overlay left of it.
  await page.mouse.move(box.x + 40, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x - 60, box.y + box.height / 2, { steps: 4 });
  await page.mouse.up();
  await expect(dialog(page, 'a')).toBeVisible();
  expect(await count(page)).toBe(1);
  // Press on the overlay, release inside the dialog.
  await page.mouse.move(box.x - 60, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 40, box.y + box.height / 2, { steps: 4 });
  await page.mouse.up();
  expect(await count(page)).toBe(1);
  // A click inside changes nothing either.
  await page.mouse.click(box.x + 40, box.y + box.height / 2);
  expect(await count(page)).toBe(1);
  await page.mouse.click(box.x - 60, box.y + box.height / 2);
  await expect(dialog(page, 'a')).toHaveCount(0);
});

test('the page behind is inert: the sidebar cannot be clicked or focused while a modal is open', async ({ page }) => {
  await boot(page);
  await open(page, 'a', { veto: true });   // stays open, so the click below can only reach the page if the shell lets it through
  await expect(page.locator('.shell')).toHaveAttribute('inert', '');
  await expect(page.locator('body')).toHaveClass(/modal-open/);
  const link = page.locator('#sidebar .sidebar-link[href="#/kanban"]');
  const box = await link.boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  expect(new URL(page.url()).hash).toBe('#/settings');
  await expect(dialog(page, 'a')).toBeVisible();
  expect(await link.evaluate((el) => { el.focus(); return document.activeElement === el; })).toBe(false);

  await page.evaluate(() => window.__h.a.close());
  await expect(page.locator('.shell')).not.toHaveAttribute('inert');
  await expect(page.locator('body')).not.toHaveClass(/modal-open/);
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect.poll(() => new URL(page.url()).hash).toBe('#/kanban');
});

test('Ctrl+K does not pull focus out of an open modal', async ({ page }) => {
  await boot(page);
  // No screen mounts the global search yet; the shortcut is checked against a stand-in so the guard is seen to matter.
  await page.evaluate(() => {
    const search = document.createElement('input');
    search.id = 'search';
    search.setAttribute('data-global-search', '');
    document.getElementById('topbar-primary').appendChild(search);
  });
  await page.keyboard.press('Control+k');
  expect(await activeId(page)).toBe('search');
  await open(page, 'a');
  await expect.poll(() => activeId(page)).toBe('a-in-0');
  await page.keyboard.press('Control+k');
  expect(await activeId(page)).toBe('a-in-0');
});

test('the overlay sits above the mobile drawer and below the conflict banner', async ({ page }) => {
  await boot(page);
  await open(page, 'a');
  expect(await page.locator('.modal-overlay').evaluate((el) => getComputedStyle(el).zIndex)).toBe('85');
  expect(await page.evaluate(() => document.querySelector('.modal-overlay').parentElement.id)).toBe('modal-host');
});

test('long content scrolls the body only; header and footer stay in view', async ({ page }) => {
  await boot(page);
  await open(page, 'a', { size: 'lg' });
  await page.evaluate(() => {
    const filler = document.createElement('div');
    filler.style.height = '4000px';
    window.__h.a.body.appendChild(filler);
  });
  const view = page.viewportSize();
  const box = await dialog(page, 'a').boundingBox();
  expect(box.y + box.height).toBeLessThanOrEqual(view.height);
  expect(await page.evaluate(() => { const b = window.__h.a.body; return b.scrollHeight > b.clientHeight; })).toBe(true);
  await expect(page.locator('.modal-header')).toBeInViewport({ ratio: 1 });
  await expect(page.locator('.modal-footer')).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => document.scrollingElement.scrollHeight <= window.innerHeight)).toBe(true);
});

test('sizes are 420, 640 and 960 wide on a desktop viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await boot(page);
  for (const [size, width] of [['sm', 420], ['md', 640], ['lg', 960]]) {
    await open(page, size, { size });
    expect((await dialog(page, size).boundingBox()).width).toBe(width);
    await page.evaluate((name) => window.__h[name].close(), size);
  }
});

test('at 390x844 the dialog fills the viewport and never widens past it', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await boot(page);
  await open(page, 'a', { size: 'lg', eyebrow: 'T-101' });
  await page.evaluate(() => window.__h.a.setTitle('A'.repeat(140)));   // an unbroken 140-character title
  // Polled: the box is 16px low while the entrance is still rising.
  await expect.poll(() => page.locator('.modal').boundingBox()).toEqual({ x: 0, y: 0, width: 390, height: 844 });
  expect(await page.locator('.modal').evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  const close = await page.locator('.modal-close').boundingBox();
  expect(Math.min(close.width, close.height)).toBeGreaterThanOrEqual(44);
  expect(close.x + close.width).toBeLessThanOrEqual(390);
});

// The Create form and the discard confirm have no eyebrow: their one-line title sat at the top of the header while the
// taller close button hung below it.
for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
  test(`at ${viewport.width}×${viewport.height} a one-line title without an eyebrow lines up with the close button`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await boot(page);
    await open(page, 'a', { size: 'md' });
    const middle = async (selector) => { const b = await page.locator(selector).boundingBox(); return b.y + b.height / 2; };
    await expect.poll(async () => Math.abs(await middle('.modal-title') - await middle('.modal-close'))).toBeLessThanOrEqual(1);
  });
}

// A two-line question is not a page: as a full-height sheet the answers sat ~700px below it on an empty screen.
test('at 390x844 a confirm stays a compact dialog with its answers right under the question', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await boot(page);
  await page.evaluate(() => { window.__answer = window.__m.confirmDialog({ title: 'Discard changes?', message: 'Your edits to this task will be lost.', confirmLabel: 'Discard', tone: 'critical' }); });
  const confirm = page.getByRole('alertdialog', { name: 'Discard changes?' });
  await expect(confirm).toBeVisible();
  await expect.poll(async () => (await confirm.boundingBox()).height).toBeLessThan(844 / 2);
  const box = await confirm.boundingBox();
  expect(box.x).toBeGreaterThan(0);
  expect(box.x + box.width).toBeLessThan(390);
  const discard = await confirm.getByRole('button', { name: 'Discard' }).boundingBox();
  // Layout lands on sub-pixel sizes (43.99998 for a 44px box): half a pixel of tolerance, never a smaller target.
  expect(Math.min(discard.width, discard.height)).toBeGreaterThanOrEqual(43.5);
  expect(discard.y + discard.height).toBeLessThanOrEqual(box.y + box.height);
});

for (const theme of ['dark', 'light']) {
  test(`no shadow on overlay or dialog; modal surfaces come from tokens (${theme})`, async ({ page }) => {
    await boot(page, { theme });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await open(page, 'a', { eyebrow: 'T-101' });
    for (const sel of ['.modal-overlay', '.modal', '.modal-header', '.modal-footer', '.modal-close']) {
      await expect(page.locator(sel)).toHaveCSS('box-shadow', 'none');
    }
    const token = (name) => page.evaluate((n) => {
      const probe = document.createElement('div');
      probe.style.color = `var(${n})`;
      document.body.appendChild(probe);
      const value = getComputedStyle(probe).color;
      probe.remove();
      return value;
    }, name);
    await expect(page.locator('.modal')).toHaveCSS('background-color', await token('--overlay-surface'));
    await expect(page.locator('.modal-title')).toHaveCSS('color', await token('--foreground-bold'));
    await expect(page.locator('.modal-eyebrow')).toHaveCSS('color', await token('--foreground-subtle'));
    await expect(page.locator('.modal-footer')).toHaveCSS('background-color', await token('--overlay-surface-sunken'));
    // Light: the dialog is the lightest surface, as a card is. Dark: RR's overlay step.
    expect(await token('--overlay-surface')).toBe(await token(theme === 'light' ? '--card-bg' : '--surface-overlay'));
    expect(await token('--overlay-surface')).not.toBe(await token('--overlay-surface-sunken'));
  });

  test(`the close button and the confirm buttons take the shared button styles (${theme})`, async ({ page }) => {
    await boot(page, { theme });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    const token = (name) => page.evaluate((n) => {
      const probe = document.createElement('div');
      probe.style.color = `var(${n})`;
      document.body.appendChild(probe);
      const value = getComputedStyle(probe).color;
      probe.remove();
      return value;
    }, name);
    await page.emulateMedia({ reducedMotion: 'reduce' });   // colours are read at rest, not mid-transition
    await page.evaluate(() => { window.__answer = window.__m.confirmDialog({ title: 'Discard', message: 'Discard your edits?', confirmLabel: 'Discard', tone: 'critical' }); });
    const close = page.locator('.modal-close');
    const cancel = page.getByRole('button', { name: 'Cancel' });
    const discard = page.getByRole('button', { name: 'Discard' });
    expect(await close.boundingBox()).toMatchObject({ width: 32, height: 32 });
    await expect(close).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    await close.hover();
    // On the dialog surface the hover fill must differ from the surface, or the hover shows nothing.
    await expect(close).toHaveCSS('background-color', await token('--overlay-surface-hover'));
    expect(await token('--overlay-surface-hover')).not.toBe(await token('--overlay-surface'));
    await expect(discard).toHaveCSS('background-color', await token('--color-critical-bold'));
    await expect(discard).toHaveCSS('color', await token('--foreground-on-accent'));
    await expect(cancel).toHaveCSS('border-top-color', await token('--border-strong'));
    await expect(cancel).toHaveCSS('color', await token('--foreground-bold'));
    for (const button of [close, cancel, discard]) {
      await expect(button).toHaveCSS('box-shadow', 'none');
      await button.hover();
      await expect(button).toHaveCSS('transform', 'none');
    }
    expect((await cancel.boundingBox()).height).toBeGreaterThanOrEqual(32);
    await cancel.click();

    await page.evaluate(() => { window.__answer = window.__m.confirmDialog({ title: 'Apply', message: 'Apply the change?' }); });
    const confirm = page.getByRole('button', { name: 'Confirm' });
    await expect(confirm).toHaveCSS('background-color', await token('--signature-fill'));
    await expect(confirm).toHaveCSS('color', await token('--on-accent-fill'));
    await confirm.click();
  });
}

// The banner asks the user to act while the edit form is open, so the keyboard must be able to reach it.
async function showBanner(page) {
  await page.evaluate(() => import('/js/components/edit/conflict-banner.js').then((b) => {
    b.showFieldConflict({
      entityKind: 'task', entityId: 'T-101', fieldKey: 'title', fieldLabel: 'Title',
      localValue: 'Mine', currentValue: 'Theirs',
      onKeepMine: async () => {}, onUseServer: () => {},
    });
  }));
  await expect(page.locator('#conflict-banner-host .cb-banner')).toBeVisible();
}

test('a conflict banner joins the Tab cycle of the open modal: banner, then dialog, wrapping both ways', async ({ page }) => {
  await boot(page);
  await open(page, 'a');
  await expect.poll(() => activeId(page)).toBe('a-in-0');
  await showBanner(page);
  expect(await activeId(page)).toBe('a-in-0');                // the banner appearing does not take focus
  await page.locator('#a-save').focus();
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('cb-use-server');         // last dialog control → the banner's first
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('cb-keep-mine');
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('modal-close');           // banner's last → the dialog's first
  await page.keyboard.press('Shift+Tab');
  expect(await activeId(page)).toBe('cb-keep-mine');          // dialog's first → the banner's last
  await page.keyboard.press('Shift+Tab');
  expect(await activeId(page)).toBe('cb-use-server');
  await page.keyboard.press('Shift+Tab');
  expect(await activeId(page)).toBe('a-save');                // banner's first → the dialog's last
  // A full lap in each direction never leaves the banner and the dialog.
  for (const key of ['Tab', 'Shift+Tab']) {
    for (let i = 0; i < 14; i++) {
      await page.keyboard.press(key);
      expect(await page.evaluate(() => window.__h.a.dialog.contains(document.activeElement)
        || document.getElementById('conflict-banner-host').contains(document.activeElement))).toBe(true);
    }
  }
  // The banner is above the overlay and takes a real click and a real key press.
  await page.locator('.cb-use-server').focus();
  // Escape typed in the banner must not dismiss the form whose conflict is being resolved.
  await page.keyboard.press('Escape');
  await expect(dialog(page, 'a')).toBeVisible();
  expect(await count(page)).toBe(1);
  await page.locator('.cb-use-server').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#conflict-banner-host .cb-banner')).toHaveCount(0);
  await expect(dialog(page, 'a')).toBeVisible();
  // With the banner gone the cycle is the dialog alone again.
  await page.keyboard.press('Tab');
  expect(await page.evaluate(() => window.__h.a.dialog.contains(document.activeElement))).toBe(true);
  await page.locator('#a-save').focus();
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('modal-close');
});

test('with two modals open the banner is reached from the top one only', async ({ page }) => {
  await boot(page);
  await open(page, 'a');
  await open(page, 'b', { size: 'sm' });
  await showBanner(page);
  await page.locator('#b-save').focus();
  await page.keyboard.press('Tab');
  expect(await activeId(page)).toBe('cb-use-server');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  expect(await page.evaluate(() => window.__h.b.dialog.contains(document.activeElement))).toBe(true);
  await page.evaluate(() => document.getElementById('conflict-banner-host').replaceChildren());
});

test('reduced motion: the entrance does not animate', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await boot(page);
  await open(page, 'a');
  const ms = (sel) => page.locator(sel).evaluate((el) => {
    const style = getComputedStyle(el);
    return { name: style.animationName, ms: parseFloat(style.animationDuration) * 1000 };
  });
  for (const sel of ['.modal-overlay', '.modal']) {
    const a = await ms(sel);
    expect(a.name).not.toBe('none');           // the keyframes are there; only their duration is taken away
    expect(a.ms).toBeLessThanOrEqual(1);
  }
});

test('without reduced motion the dialog rises over the macro duration', async ({ page }) => {
  await boot(page);
  await open(page, 'a');
  await expect(page.locator('.modal')).toHaveCSS('animation-duration', '0.4s');
  await expect(page.locator('.modal-overlay')).toHaveCSS('animation-duration', '0.2s');
});

test('confirmDialog: Enter on the focused answer resolves it; a critical confirm starts on Cancel', async ({ page }) => {
  await boot(page);
  await page.locator('#theme-toggle').focus();
  await page.evaluate(() => { window.__answer = window.__m.confirmDialog({ title: 'Apply', message: 'Apply the change?' }); });
  await expect(page.getByRole('dialog', { name: 'Apply' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Confirm' })).toBeFocused();
  await page.keyboard.press('Enter');
  expect(await page.evaluate(() => window.__answer)).toBe(true);
  await expect(page.locator('#theme-toggle')).toBeFocused();

  await page.evaluate(() => { window.__answer = window.__m.confirmDialog({ title: 'Discard', message: 'Discard your edits?', confirmLabel: 'Discard', tone: 'critical' }); });
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await expect(page.getByRole('button', { name: 'Discard' })).toHaveClass(/btn--critical/);
  await page.keyboard.press('Enter');
  expect(await page.evaluate(() => window.__answer)).toBe(false);
  expect(await count(page)).toBe(0);
});
