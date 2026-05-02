import { lessonCard } from '../components/lesson-card.js';
import * as api from '../api.js';
import { claimTopbar, tmSubcount, tmSearch, tmSegmented, tmAction } from '../lib/topbar.js';

export const meta = { title: 'Lessons', icon: '✦', sidebarKey: 'lessons' };

const SHELVES = [
  { key: 'core',    title: 'Core',    tagline: 'Frequently reinforced. Apply by default.' },
  { key: 'active',  title: 'Active',  tagline: 'Recent. Apply when anchor matches.' },
  { key: 'retired', title: 'Retired', tagline: 'No reinforcement in 30+ days. Click to revive.' },
];

export async function mount(root, { store, prefs }) {
  // Gotcha: `prefs` is the patch helper, NOT the data object.
  // Read persisted state from store.getPrefs(), then use prefs.patch() to save.
  const prefsData = store?.getPrefs?.() || {};
  const lessonsPrefs = (prefsData.screens && prefsData.screens.lessons) || {};

  root.innerHTML = '';
  const screen = document.createElement('section');
  screen.className = 'lessons';

  // ---- topbar (#topbar-actions)
  const topbar = claimTopbar();
  const subcount = tmSubcount('… lessons');
  const searchBuilt = tmSearch({
    placeholder: 'Search lessons…',
    onInput: (v) => { searchTerm = v.trim().toLowerCase(); render(); },
  });
  const initialView = lessonsPrefs.view || 'A';
  const toggle = tmSegmented(
    [
      { key: 'A', label: 'Shelves' },
      { key: 'B', label: 'Flat' },
      { key: 'C', label: 'By Anchor' },
    ],
    { value: initialView, onChange: setView },
  );
  const newBtn = tmAction({
    icon: '+', label: 'Lesson', variant: 'primary',
    title: 'New lesson — coming soon',
  });
  newBtn.setAttribute('aria-disabled', 'true');
  const catRow = document.createElement('div');
  catRow.className = 'tm-chip-row lessons__cats';

  topbar?.appendChild(subcount);
  topbar?.appendChild(searchBuilt.el);
  topbar?.appendChild(catRow);
  topbar?.appendChild(toggle);
  topbar?.appendChild(newBtn);

  // ---- shelves container
  const shelvesEl = document.createElement('div');
  shelvesEl.className = 'lessons__shelves';
  screen.appendChild(shelvesEl);

  root.appendChild(screen);

  let currentView = initialView;
  let searchTerm = '';
  const activeCategories = new Set();

  function _renderCategoryChips() {
    const lessons = store.getLessons() || [];
    const cats = [...new Set(lessons.map(l => l.category).filter(Boolean))].sort();
    catRow.innerHTML = '';
    for (const c of cats) {
      const chip = document.createElement('span');
      chip.className = 'lessons__cat-chip';
      chip.dataset.cat = c;
      chip.textContent = c;
      if (activeCategories.has(c)) chip.classList.add('is-active');
      chip.addEventListener('click', () => {
        if (activeCategories.has(c)) activeCategories.delete(c);
        else activeCategories.add(c);
        chip.classList.toggle('is-active');
        render();
      });
      catRow.appendChild(chip);
    }
  }

  function _matchesCategory(l) {
    if (activeCategories.size === 0) return true;
    return activeCategories.has(l.category);
  }
  // Track reinforced lesson IDs so re-render can re-apply is-fired state.
  const _reinforcedIds = new Set();

  function _matchesSearch(l) {
    if (!searchTerm) return true;
    const hay = [
      l.title || '',
      l.summary || '',
      l.id || '',
      ...((l.triggers && l.triggers.files) || []),
      ...((l.triggers && l.triggers.symbols) || []),
    ].join(' ').toLowerCase();
    return hay.includes(searchTerm);
  }

  function setView(v) {
    currentView = v;
    prefs.patch({ screens: { lessons: { view: v } } });
    render();
  }

  async function reinforce(id) {
    const summary = await api.reinforceLesson(id, { source: 'user', note: '' });
    // Track the reinforced id before re-render so the card can show is-fired.
    _reinforcedIds.add(id);
    // Refetch to get the updated shelf placement from server.
    const fresh = await api.getLessons();
    store.setLessons(fresh.lessons);
    render();
    return summary;
  }

  function render() {
    shelvesEl.innerHTML = '';
    const allLessons = store.getLessons() || [];
    const lessons = allLessons.filter(l => _matchesSearch(l) && _matchesCategory(l));
    const filterActive = !!searchTerm || activeCategories.size > 0;
    subcount.textContent = filterActive
      ? `${lessons.length} of ${allLessons.length} lessons`
      : `${allLessons.length} lessons`;
    // Capture reinforced IDs snapshot for post-render re-application.
    const reinforcedSnapshot = new Set(_reinforcedIds);

    if (currentView === 'A') {
      renderShelves(shelvesEl, lessons);
    } else if (currentView === 'B') {
      renderFlat(shelvesEl, lessons);
    } else {
      renderByAnchor(shelvesEl, lessons);
    }
    // Re-apply is-fired state to cards that were reinforced this session.
    if (reinforcedSnapshot.size > 0) {
      for (const id of reinforcedSnapshot) {
        const card = shelvesEl.querySelector(`[data-lesson-id="${id}"]`);
        if (card) {
          const btn = card.querySelector('.lesson-card__reinforce');
          if (btn) {
            btn.classList.add('is-fired');
            btn.textContent = '✓ Reinforced now';
            btn.disabled = true;
          }
        }
      }
    }
  }

  function renderShelves(parent, lessons) {
    for (const shelf of SHELVES) {
      const items = lessons.filter(l => (l.shelf || 'active') === shelf.key);
      const sec = document.createElement('section');
      sec.className = `lessons-shelf lessons-shelf--${shelf.key}`;
      sec.innerHTML = `
        <header class="lessons-shelf__header">
          <span class="lessons-shelf__name">${shelf.title}</span>
          <span class="lessons-shelf__tagline">${shelf.tagline}</span>
          <span class="lessons-shelf__count">${items.length} lesson${items.length === 1 ? '' : 's'}</span>
        </header>`;
      const grid = document.createElement('div');
      grid.className = 'lessons-shelf__grid';
      for (const lesson of items) {
        grid.appendChild(lessonCard(lesson, { onReinforce: reinforce }));
      }
      sec.appendChild(grid);
      parent.appendChild(sec);
    }
  }

  function renderFlat(parent, lessons) {
    const sec = document.createElement('section');
    sec.className = 'lessons-shelf lessons-shelf--flat';
    sec.innerHTML = `
      <header class="lessons-shelf__header">
        <span class="lessons-shelf__name">All lessons</span>
        <span class="lessons-shelf__tagline"></span>
        <span class="lessons-shelf__count">${lessons.length} lesson${lessons.length === 1 ? '' : 's'}</span>
      </header>`;
    const grid = document.createElement('div');
    grid.className = 'lessons-shelf__grid';
    const sorted = [...lessons].sort((a, b) => (b.reinforce_count || 0) - (a.reinforce_count || 0));
    for (const l of sorted) grid.appendChild(lessonCard(l, { onReinforce: reinforce }));
    sec.appendChild(grid);
    parent.appendChild(sec);
  }

  function renderByAnchor(parent, lessons) {
    const groups = new Map();
    for (const l of lessons) {
      const files = (l.triggers && l.triggers.files) || ['(any)'];
      for (const f of files) {
        if (!groups.has(f)) groups.set(f, []);
        groups.get(f).push(l);
      }
    }
    const sortedKeys = [...groups.keys()].sort();
    for (const key of sortedKeys) {
      const sec = document.createElement('section');
      sec.className = 'lessons-shelf';
      const header = document.createElement('header');
      header.className = 'lessons-shelf__header';
      const nameEl = document.createElement('code');
      nameEl.className = 'lessons-shelf__name';
      nameEl.textContent = key;
      const taglineEl = document.createElement('span');
      taglineEl.className = 'lessons-shelf__tagline';
      const count = groups.get(key).length;
      const countEl = document.createElement('span');
      countEl.className = 'lessons-shelf__count';
      countEl.textContent = `${count} lesson${count === 1 ? '' : 's'}`;
      header.append(nameEl, taglineEl, countEl);
      sec.appendChild(header);
      const grid = document.createElement('div');
      grid.className = 'lessons-shelf__grid';
      for (const l of groups.get(key)) grid.appendChild(lessonCard(l, { onReinforce: reinforce }));
      sec.appendChild(grid);
      parent.appendChild(sec);
    }
  }

  // First fetch (store may already have data)
  if (!store.getLessons() || store.getLessons().length === 0) {
    const data = await api.getLessons();
    store.setLessons(data.lessons);
  }
  _renderCategoryChips();
  render();

  // Cleanup function
  return () => {};
}
