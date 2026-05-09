import { bucketPhases } from '../lib/phase-buckets.js';

// Phase stepper — V12C 3-region timeline (past carousel · active card · future carousel).
//   phases:  [{ id, name, status: 'done'|'active'|'future', done, total }]
//   active:  selected filter key — phase id, '__all__', or '__orphans__'.
//   onSelect(phaseKey): callback when a chip / card / utility button is clicked.
//
// Layout:
//   [All-pill] [past-region: ‹ slide | chips | ›] [active-card] [future-region: ‹ | strip | ›] [Orphans-pill]
// Past chips are circles that morph into amber pills on selection (name reveal,
// duration scales with name length). Future cards live in a translateX strip;
// the visible card width is JS-computed so exactly VISIBLE_FUTURE cards fit.

const VISIBLE_PAST   = 3;
const VISIBLE_FUTURE = 3;
const STRIP_GAP      = 12;          // matches --sp-3
const MS_BASE        = 220;
const MS_PER_CHAR    = 16;

const chipDur = (name) => MS_BASE + String(name || '').length * MS_PER_CHAR;

export function renderPhaseStepper({ phases = [], active = '__all__', viewState, onSelect }) {
  const wrap = document.createElement('div');
  wrap.className = 'kanban-phase-stepper v12c';
  wrap.dataset.cmp = 'phase-stepper';

  // Split phases by status using the shared bucketing lib (also handles archived).
  const buckets = bucketPhases(phases);
  const pastPhases     = buckets.past;
  const futurePhases   = buckets.future;
  const activePhase    = buckets.active;
  const archivedPhases = buckets.archived;

  // Carousel offsets — owned by caller via `viewState` so re-renders preserve scroll.
  // Falls back to a fresh local object when no caller state is provided (tests / standalone use).
  const view = viewState || { pastOffset: 0, futureOffset: 0 };
  if (typeof view.pastOffset !== 'number')   view.pastOffset = 0;
  if (typeof view.futureOffset !== 'number') view.futureOffset = 0;

  // Clamp offsets in case the phase set shrank between renders.
  view.pastOffset   = Math.max(0, Math.min(view.pastOffset,   Math.max(0, pastPhases.length   - VISIBLE_PAST)));
  view.futureOffset = Math.max(0, Math.min(view.futureOffset, Math.max(0, futurePhases.length - VISIBLE_FUTURE)));

  // ── Past region: outer slide ‹  chips  inner slide › ──
  const pastRegion = document.createElement('div');
  pastRegion.className = 'phs-past-region';
  wrap.appendChild(pastRegion);

  const pastSlideOuter = makeSlideBtn('left');
  const pastSlideInner = makeSlideBtn('right');
  pastRegion.appendChild(pastSlideOuter);

  const chipEls = pastPhases.map((p) => {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'phs-chip item';
    el.dataset.key = p.id;
    el.title = `${p.name || p.id} · ${p.done || 0}/${p.total || 0}`;
    el.style.setProperty('--anim-dur', chipDur(p.name) + 'ms');
    const num = phaseNum(p);
    el.innerHTML = `<span class="num">${escapeHtml(num)}</span><span class="lbl">${escapeHtml(p.name || p.id)}</span>`;
    el.addEventListener('click', () => onSelect && onSelect(p.id));
    pastRegion.appendChild(el);
    return el;
  });
  pastRegion.appendChild(pastSlideInner);

  pastSlideOuter.addEventListener('click', () => {
    view.pastOffset = Math.min(Math.max(0, pastPhases.length - VISIBLE_PAST), view.pastOffset + VISIBLE_PAST);
    repaint();
  });
  pastSlideInner.addEventListener('click', () => {
    view.pastOffset = Math.max(0, view.pastOffset - VISIBLE_PAST);
    repaint();
  });

  // ── Active card (center) ──
  let activeCardEl = null;
  if (activePhase) {
    activeCardEl = document.createElement('button');
    activeCardEl.type = 'button';
    activeCardEl.className = 'phs-active-card' + (active === activePhase.id ? ' filtered' : '');
    activeCardEl.dataset.key = activePhase.id;
    const pct = activePhase.total ? Math.round(((activePhase.done || 0) / activePhase.total) * 100) : 0;
    activeCardEl.innerHTML = `
      <div class="head">
        <span class="num">${escapeHtml(phaseNum(activePhase))}</span>
        <span class="name">${escapeHtml(activePhase.name || activePhase.id)}</span>
        <span class="stat">${activePhase.done || 0}/${activePhase.total || 0} · ${pct}%</span>
      </div>
      <div class="pb"><i style="width:${pct}%"></i></div>
    `;
    activeCardEl.addEventListener('click', () => onSelect && onSelect(activePhase.id));
    wrap.appendChild(activeCardEl);
  }

  // ── Future region: outer slide ‹  viewport[strip]  inner slide › ──
  const futureRegion = document.createElement('div');
  futureRegion.className = 'phs-future-region';
  wrap.appendChild(futureRegion);

  const futureSlideInner = makeSlideBtn('left');
  const futureSlideOuter = makeSlideBtn('right');
  const futureViewport   = document.createElement('div');
  futureViewport.className = 'phs-future-viewport';
  const futureStrip      = document.createElement('div');
  futureStrip.className  = 'phs-future-strip';
  futureViewport.appendChild(futureStrip);
  futureRegion.appendChild(futureSlideInner);
  futureRegion.appendChild(futureViewport);
  futureRegion.appendChild(futureSlideOuter);

  const futureEls = futurePhases.map((p) => {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'phs-future-card';
    el.dataset.key = p.id;
    el.title = `${p.name || p.id} · ${p.done || 0}/${p.total || 0}`;
    const pct = p.total ? Math.round(((p.done || 0) / p.total) * 100) : 0;
    el.innerHTML = `
      <div class="head">
        <span class="num">${escapeHtml(phaseNum(p))}</span>
        <span class="name">${escapeHtml(p.name || p.id)}</span>
        <span class="stat">${p.done || 0}/${p.total || 0}</span>
      </div>
      <div class="pb"><i style="width:${pct}%"></i></div>
    `;
    el.addEventListener('click', () => onSelect && onSelect(p.id));
    futureStrip.appendChild(el);
    return el;
  });

  futureSlideInner.addEventListener('click', () => {
    view.futureOffset = Math.max(0, view.futureOffset - VISIBLE_FUTURE);
    applyFutureScroll(); updateBadges();
  });
  futureSlideOuter.addEventListener('click', () => {
    view.futureOffset = Math.min(Math.max(0, futurePhases.length - VISIBLE_FUTURE), view.futureOffset + VISIBLE_FUTURE);
    applyFutureScroll(); updateBadges();
  });

  // ── Orphans pill (right bookend) ──
  const orphansBtn = document.createElement('button');
  orphansBtn.type = 'button';
  orphansBtn.className = 'phs-util orphans-pill' + (active === '__orphans__' ? ' active' : '');
  orphansBtn.dataset.key = '__orphans__';
  orphansBtn.title = 'Tasks with no phase';
  orphansBtn.innerHTML = `<span class="util-icon">⚲</span><span class="util-text">Orphans</span>`;
  orphansBtn.addEventListener('click', () => onSelect && onSelect('__orphans__'));
  wrap.appendChild(orphansBtn);

  // ── Layout: future card width fits VISIBLE_FUTURE cards in the viewport ──
  function relayoutFuture(animate = false) {
    const vw = futureViewport.clientWidth;
    if (vw <= 0) return;
    const cardW = (vw - STRIP_GAP * (VISIBLE_FUTURE - 1)) / VISIBLE_FUTURE;
    futureEls.forEach(el => el.style.setProperty('--card-w', cardW + 'px'));
    if (!animate) {
      futureStrip.style.transition = 'none';
      applyFutureScroll();
      void futureStrip.offsetHeight;   // flush
      futureStrip.style.transition = '';
    } else {
      applyFutureScroll();
    }
  }
  function applyFutureScroll() {
    const vw = futureViewport.clientWidth;
    if (vw <= 0) return;
    const cardW = (vw - STRIP_GAP * (VISIBLE_FUTURE - 1)) / VISIBLE_FUTURE;
    const x = view.futureOffset * (cardW + STRIP_GAP);
    futureStrip.style.transform = `translateX(-${x}px)`;
  }

  function repaint() {
    const pastLen = pastPhases.length;
    const visStart = Math.max(0, pastLen - VISIBLE_PAST - view.pastOffset);
    const visEnd   = Math.max(0, pastLen - view.pastOffset);
    chipEls.forEach((el, i) => {
      el.classList.toggle('hidden',   !(i >= visStart && i < visEnd));
      el.classList.toggle('filtered', active === pastPhases[i].id);
    });
    futureEls.forEach((el, i) => el.classList.toggle('filtered', active === futurePhases[i].id));
    if (activeCardEl) activeCardEl.classList.toggle('filtered', active === activePhase.id);

    let foundFirst = false;
    pastRegion.querySelectorAll(':scope > .item').forEach(el => {
      if (el.classList.contains('hidden')) { el.classList.remove('first'); return; }
      el.classList.toggle('first', !foundFirst);
      foundFirst = true;
    });

    updateBadges();
  }

  function updateBadges() {
    const pastLen = pastPhases.length;
    const visStart = Math.max(0, pastLen - VISIBLE_PAST - view.pastOffset);
    const visEnd   = Math.max(0, pastLen - view.pastOffset);
    setBtn(pastSlideOuter, visStart);
    setBtn(pastSlideInner, pastLen - visEnd);

    const earlier = view.futureOffset;
    const later   = Math.max(0, futurePhases.length - view.futureOffset - VISIBLE_FUTURE);
    setBtn(futureSlideInner, earlier);
    setBtn(futureSlideOuter, later);
  }

  function setBtn(btn, count) {
    btn.querySelector('.badge').textContent = String(count);
    btn.classList.toggle('disabled', count <= 0);
  }

  // First paint after the node is in the DOM (so clientWidth is real).
  // We schedule via rAF and also wire a ResizeObserver for live resizes.
  requestAnimationFrame(() => {
    relayoutFuture(false);
    repaint();
  });
  const ro = new ResizeObserver(() => relayoutFuture(false));
  ro.observe(futureViewport);
  // Initial paint with the current state so the structural classes are correct
  // even before the rAF fires (avoids a flash of all chips visible).
  repaint();

  return wrap;
}

function makeSlideBtn(direction) {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'phs-slide-btn';
  const chev = direction === 'left' ? '‹' : '›';
  el.innerHTML = `<span class="chev">${chev}</span><span class="badge">0</span>`;
  return el;
}

function phaseNum(p) {
  if (p == null) return '';
  if (p.num != null) return p.num;
  // Match an integer or decimal number at the start of the id (e.g. "1", "1.5", "2").
  // Without the decimal capture, "1.5" would return "1" — indistinguishable from
  // phase "1" and misleading about the phase's position in the sequence.
  const m = String(p.id || '').match(/(\d+(?:\.\d+)?)/);
  return m ? m[1] : '';
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
