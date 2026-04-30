import { lessonCard } from '../components/lesson-card.js';
import * as api from '../api.js';

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

  // ---- header
  const header = document.createElement('header');
  header.className = 'lessons__header';
  header.innerHTML = `<h1 class="lessons__title">Lessons</h1>`;
  const toggle = document.createElement('div');
  toggle.className = 'lessons__view-toggle';
  for (const v of [['A', 'Shelves'], ['B', 'Flat'], ['C', 'By Anchor']]) {
    const b = document.createElement('button');
    b.dataset.view = v[0]; b.textContent = v[1];
    b.addEventListener('click', () => setView(v[0]));
    toggle.appendChild(b);
  }
  header.appendChild(toggle);
  screen.appendChild(header);

  // ---- shelves container
  const shelvesEl = document.createElement('div');
  shelvesEl.className = 'lessons__shelves';
  screen.appendChild(shelvesEl);

  root.appendChild(screen);

  let currentView = lessonsPrefs.view || 'A';
  function setView(v) {
    currentView = v;
    prefs.patch({ screens: { lessons: { view: v } } });
    render();
  }

  async function reinforce(id) {
    const summary = await api.reinforceLesson(id, { source: 'user', note: '' });
    // Refetch to get the updated shelf placement from server
    const fresh = await api.getLessons();
    store.setLessons(fresh.lessons);
    render();
    return summary;
  }

  function render() {
    // Highlight the active toggle button
    for (const b of toggle.querySelectorAll('button')) {
      b.classList.toggle('is-active', b.dataset.view === currentView);
    }
    shelvesEl.innerHTML = '';
    const lessons = store.getLessons() || [];

    if (currentView === 'A') {
      renderShelves(shelvesEl, lessons);
    } else if (currentView === 'B') {
      renderFlat(shelvesEl, lessons);
    } else {
      renderByAnchor(shelvesEl, lessons);
    }
  }

  function renderShelves(parent, lessons) {
    for (const shelf of SHELVES) {
      const items = lessons.filter(l => (l.shelf || 'active') === shelf.key);
      const sec = document.createElement('section');
      sec.className = `lessons-shelf lessons-shelf--${shelf.key}`;
      sec.innerHTML = `
        <header class="lessons-shelf__header">
          <span>${shelf.title} · ${items.length}</span>
          <span class="lessons-shelf__tagline">${shelf.tagline}</span>
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
        <span>All lessons · ${lessons.length}</span>
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
      sec.innerHTML = `
        <header class="lessons-shelf__header">
          <code>${key}</code><span class="lessons-shelf__tagline">${groups.get(key).length} lessons</span>
        </header>`;
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
  render();

  // Cleanup function
  return () => {};
}
