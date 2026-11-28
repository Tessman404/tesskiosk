// TessKiosk — clavier virtuel AZERTY.
// Injecté par kiosk.py dans la page (DevTools). Il s'affiche quand un champ
// de saisie reçoit le focus et se cache quand il le perd. Les touches sont
// envoyées à kiosk.py (binding « tessKbd »), qui les tape dans Chrome comme
// un vrai clavier. Sans kiosk.py, repli sur document.execCommand.
(function () {
  if (window !== window.top) {
    // Dans une page intégrée (Tessfood, Tessbet, Tessvine…) : on prévient le clavier
    // de la page principale quand un champ de saisie reçoit ou perd le focus.
    const NT = ['checkbox', 'radio', 'button', 'range', 'submit', 'color', 'file', 'hidden', 'reset', 'image'];
    const ok = (el) => el && el.nodeType === 1 && (el.isContentEditable || (!el.disabled && !el.readOnly &&
      (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && !NT.includes((el.type || 'text').toLowerCase())))));
    const kbd = () => { try { return window.top.__tessKbd || null; } catch (e) { return null; } };
    document.addEventListener('focusin', (e) => {
      const el = e.composedPath ? e.composedPath()[0] : e.target;
      const k = kbd(); if (k && ok(el)) k.show(el, []);
    }, true);
    document.addEventListener('focusout', () => { const k = kbd(); if (k) k.hide(false); }, true);
    document.addEventListener('pointerup', (e) => {
      const el = e.composedPath ? e.composedPath()[0] : e.target;
      const k = kbd(); if (k && ok(el)) setTimeout(() => k.show(el, []), 30);
    }, true);
    return;
  }
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
  // Le bouton 🏠 apparaît sur les sites ouverts depuis un lien (École Directe…)
  // et sur les pages de Home Assistant hors du tableau de bord (Music Assistant…).
  function homeButton() {
    if (!window.__tessHA) return;
    const away = () => location.origin !== window.__tessHA ||
      (window.__tessHome && !location.pathname.startsWith(window.__tessHome));
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
    const sync = () => { b.style.display = away() ? '' : 'none'; };
    sync();
    setInterval(sync, 1000);
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
      else if (d > d0 * 1.8 && d - d0 > 220 && onHA()) { done = true; window.__tessArt.start(); }
    }, { capture: true, passive: true });
    addEventListener('touchend', (e) => { if (e.touches.length < 2) d0 = 0; }, { capture: true, passive: true });
  }

  // ---------------------------------------------------------------- tableaux
  // Tableaux modernes de style scandinave, peints à la volée (plein écran) :
  // montagnes en aplats, fjords, soleil, sapins, aurores... un nouveau toutes les 15 min.
  const ART_EVERY_MS = 15 * 60 * 1000;
  const PALETTES = [
    { sky: ['#f4e9dc', '#ead2bf'], sun: '#de7a5c', layers: ['#d9c4b4', '#b9a9a3', '#8fa3a6', '#5f7e84', '#37545a', '#1f3439'] },
    { sky: ['#eef0e6', '#dfe5d2'], sun: '#d8a24a', layers: ['#c7d0b6', '#a5b595', '#7f9573', '#5c7454', '#3d5239', '#263424'] },
    { sky: ['#e9eff3', '#cddbe4'], sun: '#efc19b', layers: ['#bccbd6', '#9bb0c0', '#7790a4', '#566f84', '#3a5063', '#233545'] },
    { sky: ['#f7e7d4', '#f0c9a8'], sun: '#c5543b', layers: ['#e8b392', '#d0916f', '#a86a50', '#7c4a3a', '#4f2f27', '#2c1b17'] },
    { sky: ['#2a2f45', '#7b6990'], sun: '#f3d58c', layers: ['#5d5279', '#4a3f64', '#372f4f', '#27223b', '#1a1729', '#100e1a'] },
    { sky: ['#0d1b26', '#1f4152'], sun: '#eef0e2', layers: ['#1d3a49', '#17303d', '#122733', '#0e1f29', '#0a171f', '#060f15'], aurora: true },
    { sky: ['#f1ece4', '#e3d9cc'], sun: '#2f4858', layers: ['#d6cfc4', '#b8b2a7', '#8e938d', '#5f6b6b', '#3c4a4d', '#223034'] },
    { sky: ['#fbe8e0', '#f2cfc4'], sun: '#e9967a', layers: ['#e6c3bb', '#c9a3a6', '#a08394', '#77637c', '#4f4460', '#2e2a42'] },
  ];
  function rnd(a, b) { return a + Math.random() * (b - a); }
  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
  function ridge(W, base, amp, sharp) {
    // ligne de crête : somme de sinus (doux) ou pics anguleux (fjords)
    const pts = [], f = [rnd(.6, 1.4), rnd(1.6, 3.2), rnd(3.5, 6.5)], ph = [rnd(0, 6), rnd(0, 6), rnd(0, 6)];
    const step = sharp ? W / Math.floor(rnd(5, 11)) : W / 160;
    for (let x = -step; x <= W + step; x += step) {
      const t = x / W * Math.PI * 2;
      let y = Math.sin(t * f[0] + ph[0]) * .55 + Math.sin(t * f[1] + ph[1]) * .3 + Math.sin(t * f[2] + ph[2]) * .15;
      if (sharp) y += rnd(-.35, .35);
      pts.push([x, base - y * amp]);
    }
    return pts;
  }
  function fillRidge(g, pts, H, color) {
    g.beginPath(); g.moveTo(pts[0][0], H);
    pts.forEach(([x, y]) => g.lineTo(x, y));
    g.lineTo(pts[pts.length - 1][0], H); g.closePath(); g.fillStyle = color; g.fill();
  }
  function tree(g, x, y, h, color) {
    g.fillStyle = color; g.beginPath();
    const tiers = 4, w = h * .42;
    for (let i = 0; i < tiers; i++) {
      const ty = y - h + i * h / (tiers + .5), bw = w * (i + 1) / tiers;
      g.moveTo(x, ty); g.lineTo(x - bw / 2, ty + h / tiers * 1.25); g.lineTo(x + bw / 2, ty + h / tiers * 1.25); g.closePath();
    }
    g.fill(); g.fillRect(x - h * .02, y - h * .08, h * .04, h * .08);
  }
  function paint(canvas) {
    const W = canvas.width, H = canvas.height, g = canvas.getContext('2d');
    const P = pick(PALETTES);
    const sharp = Math.random() < .45, lake = Math.random() < .45, trees = Math.random() < .6;
    // ciel
    const sky = g.createLinearGradient(0, 0, 0, H);
    sky.addColorStop(0, P.sky[0]); sky.addColorStop(1, P.sky[1]);
    g.fillStyle = sky; g.fillRect(0, 0, W, H);
    // aurore boréale
    if (P.aurora) {
      for (let k = 0; k < 3; k++) {
        const y0 = H * rnd(.12, .3);
        g.beginPath();
        for (let x = 0; x <= W; x += 8) g.lineTo(x, y0 + Math.sin(x / W * rnd(4, 7) + k) * H * .05);
        g.strokeStyle = pick(['rgba(110,231,183,.28)', 'rgba(94,234,212,.22)', 'rgba(167,139,250,.18)']);
        g.lineWidth = H * rnd(.05, .11); g.filter = 'blur(' + Math.round(H * .025) + 'px)'; g.stroke(); g.filter = 'none';
      }
      for (let i = 0; i < 160; i++) { g.fillStyle = 'rgba(255,255,255,' + rnd(.2, .8) + ')'; g.fillRect(rnd(0, W), rnd(0, H * .55), 1.6, 1.6); }
    }
    // soleil ou lune, avec bandes façon affiche
    const sr = H * rnd(.09, .16), sx = W * rnd(.2, .8), sy = H * rnd(.22, .42);
    g.save(); g.beginPath(); g.arc(sx, sy, sr, 0, Math.PI * 2); g.fillStyle = P.sun; g.fill();
    if (Math.random() < .5) {
      g.clip(); g.fillStyle = P.sky[1];
      for (let i = 1; i < 5; i++) g.fillRect(sx - sr, sy + sr * (i / 5) * 1.0 - 2, sr * 2, sr * .045 * i);
    }
    g.restore();
    // oiseaux
    if (!P.aurora && Math.random() < .6) {
      g.strokeStyle = P.layers[4]; g.lineWidth = Math.max(2, H * .0025); g.lineCap = 'round';
      const bx = W * rnd(.3, .7), by = H * rnd(.15, .3);
      for (let i = 0; i < Math.floor(rnd(3, 7)); i++) {
        const x = bx + rnd(-W * .08, W * .08), y = by + rnd(-H * .05, H * .05), s = H * rnd(.008, .016);
        g.beginPath(); g.moveTo(x - s, y - s * .4); g.quadraticCurveTo(x - s * .4, y - s * .7, x, y);
        g.quadraticCurveTo(x + s * .4, y - s * .7, x + s, y - s * .4); g.stroke();
      }
    }
    // montagnes en couches
    const n = P.layers.length, horizon = lake ? H * .66 : H;
    for (let i = 0; i < n - (lake ? 2 : 0); i++) {
      const base = H * (.45 + i * (lake ? .04 : .1)), amp = H * (sharp ? .2 - i * .022 : .12 - i * .012);
      const pts = ridge(W, base, amp, sharp && i < 3);
      fillRidge(g, pts, horizon, P.layers[i]);
    }
    // lac avec reflets
    if (lake) {
      const lg = g.createLinearGradient(0, horizon, 0, H);
      lg.addColorStop(0, P.layers[1]); lg.addColorStop(1, P.layers[3]);
      g.fillStyle = lg; g.fillRect(0, horizon, W, H - horizon);
      g.fillStyle = P.sun; g.globalAlpha = .35;
      for (let i = 0; i < 7; i++) { const w = sr * rnd(.6, 1.8) * (1 - i / 9); g.fillRect(sx - w / 2, horizon + 8 + i * H * .03, w, Math.max(2, H * .004)); }
      g.globalAlpha = 1;
      g.fillStyle = 'rgba(255,255,255,.12)';
      for (let i = 0; i < 26; i++) g.fillRect(rnd(0, W), rnd(horizon + 10, H), rnd(W * .02, W * .08), 2);
      // rive au premier plan
      fillRidge(g, ridge(W, H * .93, H * .04, false), H, P.layers[n - 1]);
    }
    // sapins
    if (trees) {
      const groundY = lake ? H * .93 : H * .97, col = P.layers[n - 1];
      if (!lake) fillRidge(g, ridge(W, groundY, H * .03, false), H, col);
      const count = Math.floor(rnd(6, 16)), side = Math.random() < .5;
      for (let i = 0; i < count; i++) {
        const x = side ? rnd(0, W * .38) : rnd(W * .62, W), h = H * rnd(.12, .3);
        tree(g, x, groundY + rnd(0, H * .03), h, col);
      }
    }
    // grain léger façon impression
    const img = g.getImageData(0, 0, W, H), d = img.data;
    for (let i = 0; i < d.length; i += 4) { const v = (Math.random() - .5) * 14; d[i] += v; d[i + 1] += v; d[i + 2] += v; }
    g.putImageData(img, 0, 0);
  }

  const art = { el: null, timer: null };
  function artNext() {
    const r = art.el && art.el.shadowRoot;
    if (!r) return;
    const cs = r.querySelectorAll('canvas');
    const next = cs[0].classList.contains('on') ? cs[1] : cs[0];
    const cur = next === cs[0] ? cs[1] : cs[0];
    next.width = Math.round(innerWidth * (devicePixelRatio || 1));
    next.height = Math.round(innerHeight * (devicePixelRatio || 1));
    paint(next);
    next.classList.add('on'); cur.classList.remove('on');
  }
  function artStop() {
    clearInterval(art.timer);
    art.timer = null;
    if (art.el) { art.el.remove(); art.el = null; }
  }
  window.__tessArt = {
    start() {
      if (art.el) return;
      const host = document.createElement('div');
      host.id = 'tesskiosk-art';
      const r = host.attachShadow({ mode: 'open' });
      r.innerHTML = `<style>
        :host{all:initial}
        .wrap{position:fixed;inset:0;z-index:2147483646;background:#000;cursor:none;overflow:hidden}
        canvas{position:absolute;inset:0;width:100%;height:100%;opacity:0;transition:opacity 3s ease}
        canvas.on{opacity:1}
      </style><div class="wrap"><canvas></canvas><canvas></canvas></div>`;
      const t0 = Date.now();  // les doigts du geste se relèvent juste après : on les ignore
      const stop = (e) => { e.preventDefault(); e.stopPropagation(); if (Date.now() - t0 > 1200) artStop(); };
      r.querySelector('.wrap').addEventListener('click', stop, true);
      r.querySelector('.wrap').addEventListener('touchend', (e) => { if (!e.touches.length) stop(e); }, true);
      document.documentElement.appendChild(host);
      art.el = host;
      artNext();
      art.timer = setInterval(artNext, ART_EVERY_MS);
    },
    stop: artStop,
    paint,
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
