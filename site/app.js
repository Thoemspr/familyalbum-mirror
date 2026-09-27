/* Familienalbum - static gallery.

   Data is loaded in three steps so the first paint stays small:
     albums.json          -> the album switcher
     album-<id>.json      -> totals + one descriptor per month (a few KB)
     m/<id>-<YYYY-MM>.json -> the media of one month, fetched when that
                              section approaches the viewport
   Everything here is written by build_site.py. */
(() => {
  const MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
    'August', 'September', 'Oktober', 'November', 'Dezember'];
  const STORE_KEY = 'familyalbum.album';
  const album = document.getElementById('album');
  const switcher = document.getElementById('albumSwitch');
  const albumName = document.getElementById('albumName');
  const menu = document.getElementById('albumMenu');
  const statsEl = document.getElementById('stats');
  const emptyEl = document.getElementById('empty');
  const yearJump = document.getElementById('yearJump');
  const viewer = document.getElementById('viewer');
  const stage = document.getElementById('stage');
  const caption = document.getElementById('caption');

  let albums = [];            // manifest entries
  let current = null;         // id of the album on screen
  const albumCache = {};      // id -> album index payload
  let months = [];            // month descriptors of the current album, newest first
  const monthData = {};       // "<album>:<YYYY-MM>" -> entries
  const inFlight = {};        // "<album>:<YYYY-MM>" -> promise
  const sections = new Map(); // "<YYYY-MM>" -> { grid, desc }
  let observer = null;
  const visibleMonths = new Set();  // months currently in (or near) the viewport
  const failedAt = {};              // "<album>:<month>" -> time of last failure
  let flushTimer = null;
  let order = [];             // loaded entries in display order (lightbox order)
  let filter = 'all';
  let cursor = -1;            // index into order while the viewer is open
  let cursorId = null;        // uuid of the open medium, survives an order rebuild
  let viewerPushed = false;   // a history entry stands for the open viewer

  /* Album data is the one slow request in this app. Give it a deadline and one
     retry, so a stalled connection ends in a retry button instead of a spinner
     that never resolves. */
  function fetchJson(url, attempts = 2) {
    const signal = AbortSignal.timeout ? AbortSignal.timeout(45000) : undefined;
    return fetch(url, { cache: 'no-cache', signal })
      .then((response) => {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
      })
      .catch((err) => {
        if (attempts <= 1) throw err;
        return new Promise((resolve) => setTimeout(resolve, 1500))
          .then(() => fetchJson(url, attempts - 1));
      });
  }

  function retryButton(label, onClick) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'chip';
    button.textContent = label;
    button.addEventListener('click', onClick);
    return button;
  }

  function showError(message, retry) {
    statsEl.textContent = message + ' ';
    statsEl.append(retryButton('Nochmals versuchen', retry));
  }

  const pad = (n) => String(n).padStart(2, '0');
  const duration = (s) => `${Math.floor(s / 60)}:${pad(s % 60)}`;
  const fmtSize = (b) => `${(b / 1e9).toFixed(1)} GB`;
  const esc = (s) => s.replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const monthLabel = (key) => `${MONTHS[+key.slice(5) - 1]} ${key.slice(0, 4)}`;
  const cacheKey = (key) => `${current}:${key}`;
  const monthOf = (entry) => entry.t.slice(0, 7);

  // How many media of a month survive the active filter, straight from the
  // descriptor — known without loading the month itself.
  const matchCount = (desc) => (filter === 'all' ? desc.n : desc[filter]);
  const matches = (entry) => filter === 'all' || entry.k === filter;
  const shownMonths = () => months.filter((desc) => matchCount(desc) > 0);

  // --- album switching ------------------------------------------------------

  fetchJson('albums.json')
    .then((manifest) => {
      albums = manifest.albums;
      document.getElementById('generated').textContent =
        'Stand ' + new Date(manifest.generatedAt).toLocaleString('de-CH');
      albums.forEach((entry) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.role = 'menuitemradio';
        button.dataset.id = entry.id;
        const name = document.createElement('span');
        name.className = 'name';
        name.textContent = entry.name;
        const count = document.createElement('span');
        count.className = 'count';
        count.textContent = `${entry.count} Medien`;
        button.append(name, count);
        button.addEventListener('click', () => {
          closeMenu(true);
          select(entry.id);
        });
        menu.append(button);
      });
      // Nothing to switch between with a single album.
      switcher.disabled = albums.length < 2;
      const wanted = location.hash.slice(1) || localStorage.getItem(STORE_KEY);
      select(albums.some((a) => a.id === wanted) ? wanted : albums[0].id);
    })
    .catch((err) => {
      showError(`Albumliste nicht geladen (${err.message}).`, () => location.reload());
    });

  function select(id, force = false) {
    if (id === current && !force) return;
    current = id;
    localStorage.setItem(STORE_KEY, id);
    history.replaceState(null, '', '#' + id);
    [...menu.children].forEach((b) =>
      b.setAttribute('aria-checked', b.dataset.id === id));
    const meta = albums.find((a) => a.id === id);
    albumName.textContent = meta.name;
    document.title = `${meta.name} – Familienalbum`;

    const show = (payload) => {
      if (current !== id) return;  // a later click won the race
      albumCache[id] = payload;
      months = payload.months;
      statsEl.textContent = `${payload.count} Medien · ${payload.photos} Fotos · `
        + `${payload.videos} Videos · ${payload.comments} Kommentare · `
        + `${fmtSize(payload.bytes)}`
        + (payload.first ? ` · ${payload.first.slice(0, 4)}–${payload.last.slice(0, 4)}` : '');
      yearJump.length = 1;
      [...new Set(months.map((m) => m.k.slice(0, 4)))]
        .forEach((y) => yearJump.add(new Option(y, y)));
      render();
      scrollTo({ top: 0 });
    };

    if (albumCache[id]) {
      show(albumCache[id]);
      return;
    }
    statsEl.textContent = `${meta.name} wird geladen …`;
    album.textContent = '';
    emptyEl.hidden = true;
    fetchJson(meta.file)
      .then(show)
      .catch((err) => {
        if (current !== id) return;
        current = null;  // otherwise picking the same album again would be a no-op
        showError(`${meta.name} nicht geladen (${err.message}).`,
                  () => select(id, true));
      });
  }

  // --- album menu -----------------------------------------------------------

  function openMenu() {
    menu.hidden = false;
    switcher.setAttribute('aria-expanded', 'true');
    const active = menu.querySelector('[aria-checked="true"]') || menu.firstElementChild;
    if (active) active.focus();
  }

  function closeMenu(refocus = false) {
    if (menu.hidden) return;
    menu.hidden = true;
    switcher.setAttribute('aria-expanded', 'false');
    if (refocus) switcher.focus();
  }

  switcher.addEventListener('click', () => (menu.hidden ? openMenu() : closeMenu()));

  // Tap anywhere outside closes the menu, as on a native popup.
  document.addEventListener('pointerdown', (event) => {
    if (!menu.hidden && !menu.contains(event.target) && !switcher.contains(event.target)) {
      closeMenu();
    }
  });

  menu.addEventListener('keydown', (event) => {
    const items = [...menu.children];
    const at = items.indexOf(document.activeElement);
    if (event.key === 'Escape') closeMenu(true);
    else if (event.key === 'ArrowDown') items[(at + 1) % items.length].focus();
    else if (event.key === 'ArrowUp') items[(at - 1 + items.length) % items.length].focus();
    else if (event.key === 'Tab') closeMenu();
    else return;
    if (event.key !== 'Tab') event.preventDefault();
  });

  // --- rendering ------------------------------------------------------------

  function render() {
    album.textContent = '';
    sections.clear();
    if (observer) observer.disconnect();
    /* Track what is in view, but fetch only once the scrolling settles. A flick
       or a drag of the scrubber crosses every month on the way; loading each of
       them would fire twenty requests and build thousands of tiles for content
       nobody is looking at — on a phone the page simply stalls. */
    visibleMonths.clear();
    observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        const key = entry.target.dataset.month;
        if (entry.isIntersecting) visibleMonths.add(key);
        else visibleMonths.delete(key);
      });
      scheduleLoad();
    }, { rootMargin: '900px 0px' });

    const shown = shownMonths();
    emptyEl.hidden = shown.length > 0;
    shown.forEach((desc) => {
      const head = document.createElement('h2');
      head.className = 'month';
      head.id = 'm' + desc.k;
      head.textContent = monthLabel(desc.k);
      const count = document.createElement('span');
      count.textContent = matchCount(desc) === 1 ? '1 Medium'
        : `${matchCount(desc)} Medien`;
      head.append(count);
      const grid = document.createElement('div');
      grid.className = 'grid';
      grid.dataset.month = desc.k;
      album.append(head, grid);
      sections.set(desc.k, { grid, desc });

      const entries = monthData[cacheKey(desc.k)];
      if (entries) {
        fillGrid(desc.k, entries);
      } else {
        for (let i = 0; i < matchCount(desc); i += 1) {
          const box = document.createElement('div');
          box.className = 'tile skeleton';
          grid.append(box);
        }
        observer.observe(grid);
      }
    });
    rebuildOrder();
    measureScrubber();
  }

  function scheduleLoad() {
    clearTimeout(flushTimer);
    flushTimer = setTimeout(() => {
      visibleMonths.forEach((key) => loadMonth(key).catch(() => {}));
    }, 220);
  }

  /* Safety net. The observer is the fast path, but it only reports *changes* —
     a missed event, a restored scroll position, or a month whose fetch failed
     earlier can leave placeholders sitting on screen with nothing pending. Once
     a second, load whatever is visibly still unfilled. Cheap, and it makes
     "stuck on placeholders" impossible regardless of the cause. */
  setInterval(() => {
    if (document.hidden || !months.length) return;
    if (!document.querySelector('.tile.skeleton, .month-error')) return;  // all filled
    document.querySelectorAll('.grid').forEach((grid) => {
      if (!grid.querySelector('.tile.skeleton') && !grid.querySelector('.month-error')) {
        return;
      }
      const box = grid.getBoundingClientRect();
      if (box.top > innerHeight + 300 || box.bottom < -300) return;
      loadMonth(grid.dataset.month).catch(() => {});
    });
  }, 1000);

  function fillGrid(key, entries) {
    const section = sections.get(key);
    if (!section) return;
    section.grid.textContent = '';
    entries.filter(matches).forEach((entry) => section.grid.append(tile(entry)));
  }

  function loadMonth(key, force = false) {
    const id = cacheKey(key);
    if (monthData[id]) return Promise.resolve(monthData[id]);
    if (inFlight[id]) return inFlight[id];
    if (!force && failedAt[id] && Date.now() - failedAt[id] < 8000) {
      return Promise.reject(new Error('waiting after a failed attempt'));
    }
    delete failedAt[id];
    const desc = months.find((m) => m.k === key);
    if (!desc) return Promise.reject(new Error('unknown month ' + key));
    const albumId = current;
    const section = sections.get(key);
    if (section && observer) observer.unobserve(section.grid);

    inFlight[id] = fetchJson(desc.f)
      .then((payload) => {
        delete inFlight[id];
        monthData[id] = payload.media;
        if (current === albumId) {
          fillGrid(key, payload.media);
          rebuildOrder();
          measureScrubber();
        }
        return payload.media;
      })
      .catch((err) => {
        delete inFlight[id];
        failedAt[id] = Date.now();
        if (current === albumId && sections.has(key)) {
          const grid = sections.get(key).grid;
          grid.textContent = '';
          const note = document.createElement('p');
          note.className = 'month-error';
          note.textContent = `${monthLabel(key)} nicht geladen (${err.message}). `;
          note.append(retryButton('Nochmals versuchen', () => {
            grid.textContent = '';
            loadMonth(key, true).catch(() => {});
          }));
          grid.append(note);
        }
        throw err;
      });
    return inFlight[id];
  }

  function rebuildOrder() {
    order = [];
    shownMonths().forEach((desc) => {
      const entries = monthData[cacheKey(desc.k)];
      if (entries) order.push(...entries.filter(matches));
    });
    if (cursorId) {
      const index = order.findIndex((e) => e.u === cursorId);
      if (index >= 0) cursor = index;
    }
  }

  function tile(entry) {
    const button = document.createElement('button');
    button.className = 'tile';
    button.dataset.uuid = entry.u;
    button.dataset.date = entry.t.slice(0, 10);
    button.title = new Date(entry.t).toLocaleString('de-CH');
    const img = document.createElement('img');
    img.loading = 'lazy';
    img.decoding = 'async';
    img.src = entry.s || entry.p;
    img.alt = `${entry.k === 'v' ? 'Video' : 'Foto'} vom `
      + new Date(entry.t).toLocaleDateString('de-CH');
    button.append(img);
    const badges = [];
    if (entry.k === 'v') badges.push(`▶ ${entry.d ? duration(entry.d) : ''}`);
    if (entry.c) badges.push(`💬 ${entry.c.length}`);
    if (badges.length) {
      const wrap = document.createElement('div');
      wrap.className = 'badge';
      wrap.innerHTML = badges.map((b) => `<span>${b}</span>`).join('');
      button.append(wrap);
    }
    const day = document.createElement('div');
    day.className = 'day';
    day.textContent = entry.t.slice(8, 10) + '.';
    button.append(day);
    return button;
  }

  album.addEventListener('click', (event) => {
    const tileEl = event.target.closest('.tile');
    if (!tileEl || !tileEl.dataset.uuid) return;
    open(order.findIndex((e) => e.u === tileEl.dataset.uuid));
  });

  // --- viewer ---------------------------------------------------------------

  function open(index) {
    if (index < 0 || index >= order.length) return;
    cursor = index;
    const m = order[index];
    cursorId = m.u;
    stage.textContent = '';
    if (m.k === 'v') {
      const video = document.createElement('video');
      video.src = m.p;
      video.controls = true;
      video.autoplay = true;
      video.playsInline = true;
      video.poster = m.s || '';
      stage.append(video);
    } else {
      const img = document.createElement('img');
      img.src = m.p;
      img.alt = '';
      stage.append(img);
    }
    const when = new Date(m.t);
    const bits = [`<span class="when">${when.toLocaleString('de-CH', {
      weekday: 'short', day: '2-digit', month: 'long', year: 'numeric',
      hour: '2-digit', minute: '2-digit' })}</span>`];
    if (m.w && m.h) bits.push(`${m.w}×${m.h}`);
    if (m.dev) bits.push(esc(m.dev));
    if (m.gone) bits.push(`im Album gelöscht am ${m.gone}`);
    bits.push(`<a href="${m.p}" download>Original laden</a>`);
    caption.innerHTML = bits.join(' · ')
      + (m.c || []).map((c) => `<div class="comment"><b>${esc(c.n)}</b>`
        + `<time>${new Date(c.t).toLocaleString('de-CH')}</time>${esc(c.b)}</div>`).join('');

    if (viewer.hidden) {
      viewer.hidden = false;
      // One history entry for the whole lightbox session, so the phone's back
      // button returns to the grid instead of leaving the page.
      history.pushState({ viewer: true }, '');
      viewerPushed = true;
    }
    document.body.style.overflow = 'hidden';
  }

  function hideViewer() {
    viewer.hidden = true;
    stage.textContent = '';
    document.body.style.overflow = '';
    const tileEl = album.querySelector(`.tile[data-uuid="${cursorId}"]`);
    if (tileEl) tileEl.scrollIntoView({ block: 'center' });
  }

  function close() {
    if (viewerPushed) {
      viewerPushed = false;
      history.back();  // the popstate handler does the hiding
      return;
    }
    hideViewer();
  }

  addEventListener('popstate', () => {
    viewerPushed = false;
    if (!viewer.hidden) hideViewer();
  });

  /* Stepping can cross into a month that has not been fetched yet — load it,
     then continue where the rebuilt order puts us. */
  function step(delta) {
    const cur = order[cursor];
    if (!cur) return;
    const neighbour = order[cursor + delta];
    if (neighbour && monthOf(neighbour) === monthOf(cur)) {
      open(cursor + delta);
      return;
    }
    const shown = shownMonths();
    const position = shown.findIndex((desc) => desc.k === monthOf(cur));
    const next = shown[position + delta];
    if (next && !monthData[cacheKey(next.k)]) {
      stage.classList.add('busy');
      loadMonth(next.k)
        .then(() => open(cursor + delta))
        .catch(() => {})
        .finally(() => stage.classList.remove('busy'));
      return;
    }
    if (neighbour) open(cursor + delta);
  }

  viewer.querySelector('.close').addEventListener('click', close);
  viewer.querySelector('.prev').addEventListener('click', () => step(-1));
  viewer.querySelector('.next').addEventListener('click', () => step(1));
  viewer.addEventListener('click', (event) => {
    if (event.target === viewer || event.target === stage) close();
  });

  document.addEventListener('keydown', (event) => {
    if (viewer.hidden) return;
    if (event.key === 'Escape') close();
    else if (event.key === 'ArrowLeft') step(-1);
    else if (event.key === 'ArrowRight') step(1);
  });

  let touchX = null;
  viewer.addEventListener('touchstart', (e) => { touchX = e.touches[0].clientX; },
                          { passive: true });
  viewer.addEventListener('touchend', (e) => {
    if (touchX === null) return;
    const dx = e.changedTouches[0].clientX - touchX;
    if (Math.abs(dx) > 60) step(dx < 0 ? 1 : -1);
    touchX = null;
  }, { passive: true });

  // --- filters --------------------------------------------------------------

  document.querySelectorAll('.chip[data-filter]').forEach((chip) => {
    chip.addEventListener('click', () => {
      document.querySelector('.chip[data-filter].active').classList.remove('active');
      chip.classList.add('active');
      filter = chip.dataset.filter;
      render();
    });
  });

  yearJump.addEventListener('change', () => {
    const year = yearJump.value;
    if (!year) return;
    const target = [...document.querySelectorAll('h2.month')]
      .find((h) => h.id.startsWith('m' + year));
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  // --- timeline scrubber ----------------------------------------------------

  /* The strip on the right maps the document onto the scroll range: a tick at
     position p means "scrolled so that this month starts at the top". Section
     offsets are measured from the DOM, so months that still show placeholders
     are placed just as accurately as loaded ones. */
  const scrubber = document.getElementById('scrubber');
  const scrubTicks = document.getElementById('scrubTicks');
  const scrubThumb = document.getElementById('scrubThumb');
  const scrubLabel = document.getElementById('scrubLabel');
  let layout = [];        // [{ key, top, grid }] in document order
  let dragging = false;
  let scrollIdle = null;
  let framePending = false;

  const scrollMax = () =>
    Math.max(0, document.documentElement.scrollHeight - innerHeight);

  function measureScrubber() {
    layout = [];
    sections.forEach(({ grid }, key) => {
      // Measure the grid, never the month header: the headers are sticky, and
      // a pinned sticky element reports its *current* offsetTop, so at the end
      // of the page every header claims the same position.
      const head = grid.previousElementSibling;
      layout.push({
        key,
        top: grid.offsetTop - (head ? head.offsetHeight : 0),
        gridTop: grid.offsetTop,
        grid,
      });
    });
    layout.sort((a, b) => a.top - b.top);
    scrubber.hidden = layout.length < 2 || scrollMax() < innerHeight / 2;
    if (scrubber.hidden) return;
    const header = document.querySelector('header');
    document.documentElement.style.setProperty(
      '--scrub-top', `${(header ? header.getBoundingClientRect().height : 120) + 8}px`);
    renderTicks();
    updateScrubber();
  }

  function renderTicks() {
    const max = scrollMax();
    scrubTicks.textContent = '';
    let lastYear = null;
    let lastPercent = -10;
    layout.forEach(({ key, top }) => {
      const percent = Math.min(100, (top / max) * 100);
      const year = key.slice(0, 4);
      const isYear = year !== lastYear;
      if (!isYear && percent - lastPercent < 3) return;  // keep the strip legible
      if (isYear && percent - lastPercent < 1.5 && lastYear !== null) return;
      lastYear = year;
      lastPercent = percent;
      const tick = document.createElement('div');
      tick.className = isYear ? 'tick year' : 'tick';
      tick.style.top = percent + '%';
      if (isYear) tick.textContent = year;
      scrubTicks.append(tick);
    });
  }

  /* Which day sits at document offset y? The month comes from the section
     offsets; the day needs the tile grid, so it is only available once that
     month is loaded — then we read it off the tile in the row at that offset. */
  function labelAt(y) {
    let item = null;
    for (const candidate of layout) {
      if (candidate.top <= y + 4) item = candidate; else break;
    }
    if (!item) return layout.length ? monthLabel(layout[0].key) : '';
    const month = monthLabel(item.key);
    const grid = item.grid;
    const first = grid.firstElementChild;
    if (!first || !first.dataset.date) return month;
    const box = first.getBoundingClientRect();
    if (!box.height) return month;
    const style = getComputedStyle(grid);
    const columnGap = parseFloat(style.columnGap) || 0;
    const rowGap = parseFloat(style.rowGap) || 0;
    const columns = Math.max(1, Math.floor(
      (grid.clientWidth + columnGap) / (box.width + columnGap)));
    const row = Math.max(0, Math.floor((y - item.gridTop) / (box.height + rowGap)));
    const child = grid.children[Math.min(row * columns, grid.children.length - 1)];
    const date = child && child.dataset.date;
    return date ? `${+date.slice(8)}. ${month}` : month;
  }

  function updateScrubber() {
    if (scrubber.hidden) return;
    const max = scrollMax();
    const ratio = max > 0 ? Math.min(1, Math.max(0, scrollY / max)) : 0;
    scrubThumb.style.top = `${ratio * 100}%`;
    scrubLabel.textContent = labelAt(scrollY + 4);
  }

  function scrollFromPointer(clientY) {
    const rect = scrubber.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
    scrollTo({ top: ratio * scrollMax() });
  }

  scrubber.addEventListener('pointerdown', (event) => {
    dragging = true;
    scrubber.classList.add('active');
    scrubber.setPointerCapture(event.pointerId);
    scrollFromPointer(event.clientY);
    event.preventDefault();
  });
  scrubber.addEventListener('pointermove', (event) => {
    if (dragging) scrollFromPointer(event.clientY);
  });
  const endDrag = () => {
    if (!dragging) return;
    dragging = false;
    scrubber.classList.remove('active');
  };
  scrubber.addEventListener('pointerup', endDrag);
  scrubber.addEventListener('pointercancel', endDrag);

  addEventListener('scroll', () => {
    if (!framePending) {
      framePending = true;
      requestAnimationFrame(() => { framePending = false; updateScrubber(); });
    }
    scrubber.classList.add('scrolling');
    clearTimeout(scrollIdle);
    scrollIdle = setTimeout(() => scrubber.classList.remove('scrolling'), 1100);
  }, { passive: true });

  addEventListener('resize', measureScrubber);

  // The month headings stick right below the page header, whose height depends
  // on the album name and how the filters wrap — measure it instead of guessing.
  const pageHeader = document.querySelector('header');
  new ResizeObserver(() => {
    document.documentElement.style.setProperty(
      '--header-h', `${pageHeader.getBoundingClientRect().height}px`);
  }).observe(pageHeader);

  addEventListener('hashchange', () => {
    const id = location.hash.slice(1);
    if (id && albums.some((a) => a.id === id)) select(id);
  });
})();
