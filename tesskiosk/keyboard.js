// TessKiosk — clavier virtuel AZERTY.
// Injecté par kiosk.py dans la page (DevTools). Il s'affiche quand un champ
// de saisie reçoit le focus et se cache quand il le perd. Les touches sont
// envoyées à kiosk.py (binding « tessKbd »), qui les tape dans Chrome comme
// un vrai clavier. Sans kiosk.py, repli sur document.execCommand.
(function () {
  if (window !== window.top) return;           // une seule instance, dans la page principale
  if (window.__tessKbd) return;
  const NO_TEXT = ['checkbox', 'radio', 'button', 'range', 'submit', 'color', 'file', 'hidden', 'reset', 'image'];

  const LETTERS = [
    ['a', 'z', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
    ['q', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'm'],
    ['{shift}', 'w', 'x', 'c', 'v', 'b', 'n', "'", '{bksp}'],
    ['{sym}', 'é', 'è', ',', '{space}', '.', 'à', '{enter}', '{hide}'],
  ];
  const SYMBOLS = [
    ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
    ['@', '#', '&', '-', '_', '(', ')', '"', ':', '/'],
    ['ç', 'ù', 'ê', '!', '?', ';', '%', '+', '{bksp}'],
    ['{abc}', '€', '*', '=', '{space}', '°', '<', '>', '{enter}', '{hide}'],
  ];
  const LABEL = {
    '{shift}': '⇧', '{bksp}': '⌫', '{enter}': '↵', '{space}': ' ', '{sym}': '?123',
    '{abc}': 'ABC', '{hide}': '⌄',
  };

  let layer = 'abc', shift = false, visible = false, target = null, hideTimer = null;

  // ---------------------------------------------------------------- interface
  const host = document.createElement('div');
  host.id = 'tesskiosk-keyboard';
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `<style>
    :host { all: initial; }
    .kb { position: fixed; left: 0; right: 0; bottom: 0; z-index: 2147483647;
      background: rgba(20, 14, 34, .97); border-top: 1px solid rgba(255,255,255,.12);
      box-shadow: 0 -8px 30px rgba(0,0,0,.45); padding: 10px 10px 14px;
      font-family: Roboto, 'Liberation Sans', sans-serif; user-select: none; -webkit-user-select: none;
      touch-action: none; transform: translateY(110%); transition: transform .18s ease-out; }
    .kb.on { transform: none; }
    .rows { max-width: 1500px; margin: 0 auto; display: flex; flex-direction: column; gap: 8px; }
    .row { display: flex; gap: 8px; justify-content: center; }
    .k { flex: 1 1 0; max-width: 140px; height: 64px; border-radius: 12px; display: flex;
      align-items: center; justify-content: center; font-size: 27px; color: #f1f5f9;
      background: rgba(255,255,255,.10); border: 1px solid rgba(255,255,255,.08); }
    .k.fn { background: rgba(168,85,247,.28); font-size: 22px; }
    .k.space { flex: 5 1 0; max-width: none; }
    .k.enter { background: rgba(74,222,128,.30); }
    .k.active { background: rgba(168,85,247,.75); }
    .k.down { background: rgba(255,255,255,.35); }
  </style><div class="kb"><div class="rows"></div></div>`;
  const kb = root.querySelector('.kb');
  const rowsEl = root.querySelector('.rows');

  function draw() {
    const rows = layer === 'abc' ? LETTERS : SYMBOLS;
    rowsEl.innerHTML = '';
    for (const r of rows) {
      const row = document.createElement('div');
      row.className = 'row';
      for (const key of r) {
        const b = document.createElement('div');
        b.className = 'k';
        b.dataset.key = key;
        if (key.startsWith('{')) b.classList.add('fn');
        if (key === '{space}') b.classList.add('space');
        if (key === '{enter}') b.classList.add('enter');
        if (key === '{shift}' && shift) b.classList.add('active');
        b.textContent = LABEL[key] !== undefined ? LABEL[key]
          : (shift && layer === 'abc' ? key.toUpperCase() : key);
        row.appendChild(b);
      }
      rowsEl.appendChild(row);
    }
  }

  // Les touches ne doivent jamais prendre le focus au champ de saisie
  host.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const b = e.composedPath().find((n) => n.classList && n.classList.contains('k'));
    if (!b) return;
    b.classList.add('down');
    setTimeout(() => b.classList.remove('down'), 120);
    press(b.dataset.key);
  }, { capture: true, passive: false });
  for (const ev of ['mousedown', 'touchstart']) {
    host.addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); }, { capture: true, passive: false });
  }
  for (const ev of ['click', 'mouseup', 'touchend', 'pointerup']) {
    host.addEventListener(ev, (e) => e.stopPropagation(), true);
  }

  // ---------------------------------------------------------------- frappe
  function send(msg) {
    if (typeof window.tessKbd === 'function') {
      window.tessKbd(JSON.stringify(msg));
      return;
    }
    // Repli : sans kiosk.py on tape directement dans le champ
    const doc = (target && target.ownerDocument) || document;
    if (msg.t === 'text') doc.execCommand('insertText', false, msg.v);
    else if (msg.k === 'Backspace') doc.execCommand('delete');
    else if (msg.k === 'Enter') {
      const o = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, composed: true };
      target && target.dispatchEvent(new KeyboardEvent('keydown', o));
      target && target.dispatchEvent(new KeyboardEvent('keyup', o));
    }
  }

  function press(key) {
    switch (key) {
      case '{shift}': shift = !shift; draw(); return;
      case '{sym}': layer = 'sym'; draw(); return;
      case '{abc}': layer = 'abc'; draw(); return;
      case '{hide}': hide(true); return;
      case '{bksp}': send({ t: 'key', k: 'Backspace' }); return;
      case '{enter}': send({ t: 'key', k: 'Enter' }); return;
      case '{space}': send({ t: 'text', v: ' ' }); return;
    }
    send({ t: 'text', v: shift && layer === 'abc' ? key.toUpperCase() : key });
    if (shift) { shift = false; draw(); }
  }

  // ---------------------------------------------------------------- focus
  function editable(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.isContentEditable) return true;
    if (el.disabled || el.readOnly) return false;
    if (el.tagName === 'TEXTAREA') return true;
    return el.tagName === 'INPUT' && !NO_TEXT.includes((el.type || 'text').toLowerCase());
  }

  function deepActive(doc) {
    let el = doc.activeElement;
    for (let i = 0; i < 30 && el; i++) {
      if (el.shadowRoot && el.shadowRoot.activeElement) { el = el.shadowRoot.activeElement; continue; }
      if (el.tagName === 'IFRAME') {
        let d = null;
        try { d = el.contentDocument; } catch (e) { d = null; }
        if (d && d.activeElement) { watch(d); el = d.activeElement; continue; }
      }
      break;
    }
    return el;
  }

  function show(el, path) {
    clearTimeout(hideTimer);
    target = el;
    // Dans une boîte de dialogue « modale », le reste de la page ne réagit plus :
    // le clavier se place alors à l'intérieur de la boîte de dialogue.
    const dlg = (path || []).find((n) => n && n.tagName === 'DIALOG' && n.open);
    const parent = dlg || document.body;
    if (host.parentNode !== parent) parent.appendChild(host);
    if (!visible) { layer = 'abc'; shift = false; draw(); }
    visible = true;
    requestAnimationFrame(() => kb.classList.add('on'));
    setTimeout(() => { try { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {} }, 200);
  }

  function hide(now) {
    clearTimeout(hideTimer);
    const go = () => { visible = false; target = null; kb.classList.remove('on'); };
    if (now) go(); else hideTimer = setTimeout(() => { if (!editable(deepActive(document))) go(); }, 150);
  }

  function onFocusIn(e) {
    const path = e.composedPath ? e.composedPath() : [e.target];
    const el = path[0];
    if (el && el.tagName === 'IFRAME') {
      setTimeout(() => { const a = deepActive(document); if (editable(a)) show(a, []); }, 50);
      return;
    }
    if (editable(el)) show(el, path);
  }

  const watched = new WeakSet();
  function watch(doc) {
    if (!doc || watched.has(doc)) return;
    watched.add(doc);
    doc.addEventListener('focusin', onFocusIn, true);
    doc.addEventListener('focusout', () => { if (visible) hide(false); }, true);
    // Un toucher dans un champ déjà actif (clavier caché avec ⌄) le rouvre
    doc.addEventListener('pointerup', (e) => {
      const el = e.composedPath ? e.composedPath()[0] : e.target;
      if (!visible && editable(el) && deepActive(document) === el) show(el, e.composedPath());
    }, true);
  }

  // Bouton 🏠 sur les sites ouverts depuis un lien (École Directe…) :
  // ramène au tableau de bord (kiosk.py ferme l'onglet du site)
  function homeButton() {
    if (!window.__tessHA || location.origin === window.__tessHA) return;
    const b = document.createElement('div');
    b.id = 'tesskiosk-home';
    const r = b.attachShadow({ mode: 'open' });
    r.innerHTML = `<style>:host{all:initial}
      .h{position:fixed;left:0;top:50%;transform:translateY(-50%);z-index:2147483647;width:58px;height:64px;
        border-radius:0 18px 18px 0;background:rgba(124,58,237,.92);box-shadow:0 4px 16px rgba(0,0,0,.45);
        display:flex;align-items:center;justify-content:center;font-size:30px;cursor:pointer;user-select:none}</style>
      <div class="h" title="Retour au tableau de bord">🏠</div>`;
    r.querySelector('.h').addEventListener('pointerdown', (e) => {
      e.preventDefault(); e.stopPropagation();
      if (typeof window.tessKbd === 'function') window.tessKbd(JSON.stringify({ t: 'home' }));
      else location.href = window.__tessHA;
    }, true);
    document.documentElement.appendChild(b);
  }

  // ---------------------------------------------------------------- gestes
  // Pincer vers l'intérieur : retour à l'accueil (et fermeture des sites ouverts).
  // Écarter deux doigts (sur Home Assistant) : tableaux de paysages.
  function gestures() {
    let d0 = 0, done = false;
    const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
    const onHA = () => !window.__tessHA || location.origin === window.__tessHA;
    const ask = (t) => {
      if (typeof window.tessKbd === 'function') window.tessKbd(JSON.stringify({ t }));
      else if (t === 'home' && !onHA()) location.href = window.__tessHA;
    };
    addEventListener('touchstart', (e) => {
      if (e.touches.length === 2) { d0 = dist(e.touches); done = false; }
    }, { capture: true, passive: true });
    addEventListener('touchmove', (e) => {
      if (e.touches.length !== 2 || !d0 || done) return;
      const d = dist(e.touches);
      if (d < d0 * 0.55) { done = true; ask('home'); }
      else if (d > d0 * 1.8 && d - d0 > 220 && onHA()) { done = true; ask('art'); }
    }, { capture: true, passive: true });
    addEventListener('touchend', (e) => { if (e.touches.length < 2) d0 = 0; }, { capture: true, passive: true });
  }

  // ---------------------------------------------------------------- tableaux
  const ART_EVERY_MS = 15 * 60 * 1000;
  const art = { el: null, timer: null, list: [], i: 0, port: 8099 };
  function artShow(item) {
    const r = art.el.shadowRoot;
    const url = 'http://127.0.0.1:' + art.port + '/art/' + item.file;
    const pre = new Image();
    pre.onload = () => {
      const layers = r.querySelectorAll('.layer');
      const next = layers[0].classList.contains('on') ? layers[1] : layers[0];
      const cur = next === layers[0] ? layers[1] : layers[0];
      next.querySelector('.bg').style.backgroundImage = 'url("' + url + '")';
      next.querySelector('img').src = url;
      next.querySelector('.cap').textContent =
        [item.title, [item.artist, item.date].filter(Boolean).join(', ')].filter(Boolean).join(' — ');
      next.classList.add('on');
      cur.classList.remove('on');
    };
    pre.onerror = () => artNext();
    pre.src = url;
  }
  function artNext() {
    if (!art.list.length) return;
    art.i = (art.i + 1) % art.list.length;
    artShow(art.list[art.i]);
  }
  function artStop() {
    clearInterval(art.timer);
    art.timer = null;
    if (art.el) { art.el.remove(); art.el = null; }
  }
  window.__tessArt = {
    start(list, port) {
      if (art.el || !list || !list.length) return;
      art.port = port || 8099;
      art.list = list.slice().sort(() => Math.random() - 0.5);
      art.i = 0;
      const host = document.createElement('div');
      host.id = 'tesskiosk-art';
      const r = host.attachShadow({ mode: 'open' });
      const layer = `<div class="layer"><div class="bg"></div><img alt=""><div class="cap"></div></div>`;
      r.innerHTML = `<style>
        :host{all:initial}
        .wrap{position:fixed;inset:0;z-index:2147483646;background:#0b0b0d;cursor:pointer;overflow:hidden}
        .layer{position:absolute;inset:0;opacity:0;transition:opacity 2.5s ease}
        .layer.on{opacity:1}
        .bg{position:absolute;inset:-40px;background-size:cover;background-position:center;filter:blur(38px) brightness(.45) saturate(1.1)}
        img{position:absolute;inset:0;margin:auto;max-width:100%;max-height:100%;width:100%;height:100%;object-fit:contain;
          filter:drop-shadow(0 18px 40px rgba(0,0,0,.6))}
        .cap{position:absolute;left:28px;bottom:20px;max-width:70%;font:13px/1.4 Roboto,'Liberation Sans',sans-serif;
          color:rgba(255,255,255,.55);letter-spacing:.3px;text-shadow:0 1px 3px rgba(0,0,0,.8)}
      </style><div class="wrap">${layer}${layer}</div>`;
      const t0 = Date.now();  // les doigts du geste se relèvent juste après : on les ignore
      const stop = (e) => { e.preventDefault(); e.stopPropagation(); if (Date.now() - t0 > 1200) artStop(); };
      r.querySelector('.wrap').addEventListener('click', stop, true);
      r.querySelector('.wrap').addEventListener('touchend', (e) => { if (!e.touches.length) stop(e); }, true);
      document.documentElement.appendChild(host);
      art.el = host;
      artShow(art.list[0]);
      art.timer = setInterval(artNext, ART_EVERY_MS);
    },
    stop: artStop,
  };

  function start() {
    homeButton();
    gestures();
    if (window.__tessKbdOff) return;
    watch(document);
    document.body.appendChild(host);
    draw();
  }
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
  window.__tessKbd = { show, hide };
})();
