"""TessKiosk session manager.

- Logs Chromium into Home Assistant (tokens injected through the DevTools
  protocol, so the login page never shows), then opens the dashboard.
- Watches the page and logs in again if Home Assistant asks for it.
- Small local HTTP API (127.0.0.1 only) for automations:
    GET  /status
    POST /reload
    POST /home
    POST /navigate   {"path": "/cuisine-24/cameras"}
    POST /screen/on  /screen/off
"""
import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websocket  # python3-websocket

HA = os.environ.get("HA_URL", "http://127.0.0.1:8123").rstrip("/")
CLIENT_ID = HA + "/"
USER = os.environ["HA_USERNAME"]
PASSWORD = os.environ["HA_PASSWORD"]
DASHBOARD = os.environ.get("DASHBOARD", "").strip("/")
API_PORT = int(os.environ.get("API_PORT", "8099"))
CDP = "http://127.0.0.1:9222"
HOME_URL = f"{HA}/{DASHBOARD}" if DASHBOARD else HA + "/"
RETURN_HOME_S = int(os.environ.get("RETURN_HOME_MINUTES", "5") or 0) * 60
home_paths = {urllib.parse.urlparse(HOME_URL).path.rstrip("/") or "/"}

# Installed in the page: remembers the last time someone touched / moved / typed
IDLE_JS = """(() => {
  const mark = () => { window.__tkIdle = Date.now(); };
  const EV = ['pointerdown','pointermove','touchstart','keydown','wheel'];
  if (!window.__tkIdle) {
    window.__tkIdle = Date.now();
    EV.forEach(e => addEventListener(e, mark, {capture: true, passive: true}));
  }
  // also listen inside the add-on pages (Tessbet, Tessfood...), shown in iframes
  const frames = [];
  const walk = (root) => root.querySelectorAll('iframe, *').forEach(el => {
    if (el.tagName === 'IFRAME') frames.push(el);
    if (el.shadowRoot) walk(el.shadowRoot);
  });
  try { walk(document); } catch (e) {}
  frames.forEach(f => { try {
    const w = f.contentWindow;
    if (w && !w.__tkHooked) { w.__tkHooked = true; EV.forEach(e => w.addEventListener(e, mark, {capture: true, passive: true})); }
  } catch (e) {} });
  // a camera shown in large (zoom) stays on screen: no return to the home page
  let zoom = false;
  const find = (root) => {
    if (zoom) return;
    if (root.querySelector('advanced-camera-card[expanded], [data-grand]')) { zoom = true; return; }
    root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) find(el.shadowRoot); });
  };
  try { find(document); } catch (e) {}
  return [Date.now() - window.__tkIdle, location.pathname, zoom];
})()"""

GO_HOME_JS = """((p) => {
  history.pushState(null, '', p);
  window.dispatchEvent(new CustomEvent('location-changed', {detail: {replace: false}}));
  window.__tkIdle = Date.now();
  return true;
})(%s)"""

lock = threading.RLock()
# Local connections only: never go through a proxy
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


# ---------------------------------------------------------------- HTTP utils
def http(method, url, data=None, form=False, timeout=10):
    headers = {}
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with OPENER.open(req, timeout=timeout) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)


def wait_for(url, what, timeout=600):
    start = time.time()
    while time.time() - start < timeout:
        try:
            http("GET", url, timeout=5)
            return True
        except Exception:
            time.sleep(2)
    log(f"{what} ne répond pas après {timeout}s")
    return False


# ------------------------------------------------------------- HA auth flow
def login():
    """Full username/password login flow -> token dict."""
    _, flow = http("POST", f"{HA}/auth/login_flow", {
        "client_id": CLIENT_ID,
        "handler": ["homeassistant", None],
        "redirect_uri": CLIENT_ID + "?auth_callback=1",
    })
    _, step = http("POST", f"{HA}/auth/login_flow/{flow['flow_id']}", {
        "client_id": CLIENT_ID,
        "username": USER,
        "password": PASSWORD,
    })
    if step.get("type") != "create_entry":
        raise RuntimeError("connexion refusée par Home Assistant (identifiant ou mot de passe ?) : "
                           + str(step.get("errors")))
    _, tok = http("POST", f"{HA}/auth/token", {
        "grant_type": "authorization_code",
        "code": step["result"],
        "client_id": CLIENT_ID,
    }, form=True)
    log("Connexion à Home Assistant réussie")
    return tok


def refresh(refresh_token):
    """Returns a fresh token dict, or None if the refresh token is no longer valid."""
    try:
        _, tok = http("POST", f"{HA}/auth/token", {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        }, form=True)
        tok["refresh_token"] = refresh_token
        return tok
    except urllib.error.HTTPError:
        return None


# -------------------------------------------------------------- DevTools
def all_pages():
    try:
        _, targets = http("GET", f"{CDP}/json", timeout=3)
    except Exception:
        return []
    return [t for t in targets if t.get("type") == "page"
            and not t.get("url", "").startswith(("chrome://", "devtools://", "chrome-extension://"))]


def page_target():
    """The Home Assistant page (other tabs are sites opened from a link)."""
    pages = all_pages()
    ha = [t for t in pages if t.get("url", "").startswith(HA)]
    return (ha or pages or [None])[0]


def close_target(t):
    try:
        http("GET", f"{CDP}/json/close/{t['id']}", timeout=3)
    except Exception:
        pass


class Page:
    def __init__(self, target):
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"],
                                              timeout=20, suppress_origin=True,
                                              http_no_proxy=["127.0.0.1", "localhost"])
        self.n = 0

    def call(self, method, **params):
        self.n += 1
        my_id = self.n
        self.ws.send(json.dumps({"id": my_id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == my_id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expr):
        res = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return res.get("result", {}).get("value")

    def goto(self, url, settle=2.0):
        self.call("Page.navigate", url=url)
        time.sleep(settle)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def with_page(fn):
    with lock:
        target = page_target()
        if not target:
            raise RuntimeError("Chrome n'est pas prêt")
        p = Page(target)
        try:
            return fn(p, target)
        finally:
            p.close()


# --------------------------------------------------------- virtual keyboard
VIRTUAL_KEYBOARD = os.environ.get("VIRTUAL_KEYBOARD", "true") == "true"
KEYS = {  # key -> (code, windows virtual key code, text typed)
    "Backspace": ("Backspace", 8, ""),
    "Enter": ("Enter", 13, "\r"),
    "Tab": ("Tab", 9, ""),
    "Escape": ("Escape", 27, ""),
    "ArrowLeft": ("ArrowLeft", 37, ""),
    "ArrowRight": ("ArrowRight", 39, ""),
}


def page_helper(target, js):
    """One DevTools connection per tab: injects keyboard.js on every page load
    and types the keys sent by the on-screen keyboard."""
    ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=20,
                                     suppress_origin=True,
                                     http_no_proxy=["127.0.0.1", "localhost"])
    n = [0]

    def send(method, **params):
        n[0] += 1
        ws.send(json.dumps({"id": n[0], "method": method, "params": params}))

    try:
        send("Page.enable")
        send("Runtime.enable")
        send("Runtime.addBinding", name="tessKbd")
        send("Page.addScriptToEvaluateOnNewDocument", source=js)
        send("Runtime.evaluate", expression=js)
        ws.settimeout(None)
        while True:
            msg = json.loads(ws.recv())
            if msg.get("method") != "Runtime.bindingCalled":
                continue
            params = msg.get("params", {})
            if params.get("name") != "tessKbd":
                continue
            try:
                k = json.loads(params.get("payload") or "{}")
            except ValueError:
                continue
            if k.get("t") == "text" and k.get("v"):
                send("Input.insertText", text=str(k["v"])[:20])
            elif k.get("t") == "key" and k.get("k") in KEYS:
                code, vk, text = KEYS[k["k"]]
                down = {"key": k["k"], "code": code, "windowsVirtualKeyCode": vk,
                        "nativeVirtualKeyCode": vk}
                if text:
                    send("Input.dispatchKeyEvent", type="keyDown", text=text,
                         unmodifiedText=text, **down)
                else:
                    send("Input.dispatchKeyEvent", type="rawKeyDown", **down)
                send("Input.dispatchKeyEvent", type="keyUp", **down)
            elif k.get("t") == "home":
                go_home(target)
            elif k.get("t") == "art":
                pics = art_list()
                if pics:
                    send("Runtime.evaluate", expression="window.__tessArt && window.__tessArt.start(%s, %d)"
                         % (json.dumps(pics), API_PORT))
                else:
                    log("Tableaux : la bibliothèque n'est pas encore prête")
    finally:
        try:
            ws.close()
        except Exception:
            pass


def go_home(target=None):
    """Back to the dashboard: closes the tabs opened from links (École Directe...)."""
    main_page = page_target()
    for t in all_pages():
        if main_page and t["id"] != main_page["id"] and not t.get("url", "").startswith(HA):
            close_target(t)
    if main_page:
        try:
            http("GET", f"{CDP}/json/activate/{main_page['id']}", timeout=3)
        except Exception:
            pass
        if not main_page.get("url", "").startswith(HA):
            with_page(lambda p, t: p.goto(HOME_URL, settle=0))
        elif urllib.parse.urlparse(main_page.get("url", "")).path.rstrip("/") not in home_paths:
            target_path = sorted(home_paths, key=len)[-1]
            with_page(lambda p, t: p.eval(GO_HOME_JS % json.dumps(target_path)))
    log("Retour au tableau de bord")


def helpers_loop():
    """Watches the tabs and gives each one the keyboard and the home button."""
    try:
        with open("/keyboard.js", encoding="utf-8") as f:
            js = ("window.__tessHA=%s;window.__tessKbdOff=%s;\n" %
                  (json.dumps(urllib.parse.urlparse(HA)._replace(path="").geturl()),
                   "false" if VIRTUAL_KEYBOARD else "true")) + f.read()
    except OSError as e:
        log(f"Clavier virtuel indisponible : {e}")
        return
    running = {}
    log("Clavier virtuel " + ("prêt" if VIRTUAL_KEYBOARD else "désactivé") + ", bouton d'accueil sur les autres sites")
    while True:
        for t in all_pages():
            th = running.get(t["id"])
            if th and th.is_alive():
                continue

            def run(t=t):
                try:
                    page_helper(t, js)
                except Exception:
                    pass
            running[t["id"]] = threading.Thread(target=run, daemon=True)
            running[t["id"]].start()
        for tid in [i for i, th in running.items() if not th.is_alive()]:
            running.pop(tid, None)
        time.sleep(2)


# ----------------------------------------------------------------- session
def ensure_session(force_login=False):
    def run(p, target):
        if not target.get("url", "").startswith(HA):
            p.goto(f"{HA}/manifest.json")
        stored = p.eval("localStorage.getItem('hassTokens')")
        tok = None
        if stored and not force_login:
            try:
                tok = refresh(json.loads(stored).get("refresh_token", ""))
            except Exception:
                tok = None
        if tok is None:
            tok = login()
        tok.update({
            "hassUrl": HA,
            "clientId": CLIENT_ID,
            "expires": int(time.time() * 1000) + int(tok.get("expires_in", 1800)) * 1000,
        })
        p.eval("localStorage.setItem('hassTokens', %s);"
               "localStorage.setItem('dockedSidebar', '\"always_hidden\"');"
               "true" % json.dumps(json.dumps(tok)))
        p.goto(HOME_URL, settle=4.0)
        landing = (p.eval("location.pathname") or "").rstrip("/")
        if landing:
            home_paths.add(landing)
        log(f"Tableau de bord ouvert : {HOME_URL}")
    return with_page(run)


def log_gpu():
    """Log whether Chromium really uses the GPU (compositing, raster, video)."""
    try:
        _, ver = http("GET", f"{CDP}/json/version", timeout=3)
        ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=20, suppress_origin=True,
                                         http_no_proxy=["127.0.0.1", "localhost"])
        ws.send(json.dumps({"id": 1, "method": "SystemInfo.getInfo"}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                break
        ws.close()
        gpu = msg.get("result", {}).get("gpu", {})
        fs = gpu.get("featureStatus", {})
        keys = ("gpu_compositing", "rasterization", "video_decode", "webgl", "opengl", "skia_graphite")
        log("Puce graphique dans Chrome : " + ", ".join(f"{k}={fs.get(k)}" for k in keys if k in fs))
        dev = (gpu.get("devices") or [{}])[0]
        log(f"  carte : {dev.get('vendorString') or dev.get('vendorId')} / {dev.get('deviceString') or dev.get('deviceId')} pilote {dev.get('driverVendor', '')} {dev.get('driverVersion', '')}")
        for p in (gpu.get("driverBugWorkarounds") or [])[:0]:
            pass
    except Exception as e:
        log(f"Infos puce graphique indisponibles : {e}")


def needs_login(url):
    return (not url or "/auth/authorize" in url or url.startswith("chrome-error")
            or url.startswith("about:") or url.endswith("/manifest.json"))


def watchdog():
    last_reload_day = None
    while True:
        time.sleep(15)
        try:
            target = page_target()
            if not target:
                continue
            url = target.get("url", "")
            if needs_login(url):
                log(f"Session à rétablir (page : {url[:80]})")
                ensure_session(force_login="/auth/authorize" in url)
                continue
            # back to the home page after a while without anybody touching the screen
            if RETURN_HOME_S:
                res = list(with_page(lambda p, t: p.eval(IDLE_JS)) or [])
                idle_ms, path, zoom = (res + [0, "", False][len(res):])[:3]
                if zoom:
                    pass  # camera in large: stays as long as wanted
                elif idle_ms >= RETURN_HOME_S * 1000 and (path or "").rstrip("/") not in home_paths:
                    target_path = sorted(home_paths, key=len)[-1]
                    with_page(lambda p, t: p.eval(GO_HOME_JS % json.dumps(target_path)))
                    log(f"Retour à l'accueil après {RETURN_HOME_S // 60} min sans activité (depuis {path})")
            # sites opened from a link (École Directe...): closed after the same idle time
            if RETURN_HOME_S:
                for t in all_pages():
                    if t["id"] == target["id"] or t.get("url", "").startswith(HA):
                        continue
                    try:
                        pg = Page(t)
                        try:
                            idle_ms = (pg.eval(IDLE_JS) or [0])[0]
                        finally:
                            pg.close()
                    except Exception:
                        continue
                    if idle_ms >= RETURN_HOME_S * 1000:
                        go_home()
                        log(f"Site fermé après {RETURN_HOME_S // 60} min sans activité : {t.get('url', '')[:60]}")
                        break
            # one quiet reload per night to keep memory low
            now = time.localtime()
            if now.tm_hour == 4 and last_reload_day != now.tm_yday:
                last_reload_day = now.tm_yday
                with_page(lambda p, t: p.call("Page.reload", ignoreCache=False))
                log("Rechargement nocturne")
        except Exception as e:  # keep watching whatever happens
            log(f"Surveillance : {e}")


# ------------------------------------------------------------ art library
# Paysages scandinaves du domaine public (peintres morts il y a plus de 70 ans),
# téléchargés une fois depuis Wikimedia Commons dans /data/art.
ART_DIR = "/data/art"
ART_CATEGORIES = [
    "Paintings by Harald Sohlberg", "Paintings by Peder Balke", "Paintings by Gustaf Fjæstad",
    "Paintings by Hans Gude", "Paintings by Johan Christian Dahl", "Paintings by Thomas Fearnley",
    "Paintings by Frits Thaulow", "Paintings by Anna Boberg", "Paintings by Prince Eugen",
    "Paintings by Eero Järnefelt", "Paintings by Pekka Halonen", "Paintings by Nikolai Astrup",
    "Paintings by Akseli Gallen-Kallela", "Paintings by Kitty Kielland", "Paintings by Eilif Peterssen",
    "Paintings by Alfred Wahlberg", "Paintings by Theodor Kittelsen", "Paintings by Fanny Churberg",
    "Paintings by Victor Westerholm", "Paintings by Werner Holmberg",
]
ART_PER_PAINTER = 5
ART_SKIP = ("portrait", "self", "selvportrett", "självporträtt", "study", "studie", "sketch", "skisse",
            "drawing", "tegning", "detail", "frame", "signature", "letter", "museum", "exhibition",
            "photo", "foto", "woman", "man ", "girl", "boy", "kvinne", "mann", "pike", "gutt",
            "interior", "interiør", "still life", "stilleben", "nude", "akt", "x-ray", "back")
ART_UA = "TessKiosk/0.7 (https://github.com/Tessman404/tesskiosk) home kiosk art frame"
art_lock = threading.Lock()


def art_list():
    try:
        with open(os.path.join(ART_DIR, "index.json"), encoding="utf-8") as f:
            items = json.load(f)
        return [i for i in items if os.path.exists(os.path.join(ART_DIR, i["file"]))]
    except Exception:
        return []


def web_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": ART_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def clean_html(t):
    import re
    import html as h
    return h.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()


def build_art():
    import random
    os.makedirs(ART_DIR, exist_ok=True)
    have = art_list()
    if len(have) >= 40:
        return
    items, seen = list(have), {i["file"] for i in have}
    for cat in ART_CATEGORIES:
        q = urllib.parse.urlencode({
            "action": "query", "format": "json", "generator": "categorymembers",
            "gcmtitle": "Category:" + cat, "gcmtype": "file", "gcmlimit": "200",
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "1920",
            "iiextmetadatafilter": "ObjectName|Artist|DateTimeOriginal",
        })
        try:
            data = json.loads(web_get("https://commons.wikimedia.org/w/api.php?" + q))
        except Exception as e:
            log(f"Tableaux : {cat} indisponible ({e.__class__.__name__})")
            continue
        cands = []
        for pg in (data.get("query", {}).get("pages", {}) or {}).values():
            ii = (pg.get("imageinfo") or [{}])[0]
            w, h = ii.get("width", 0), ii.get("height", 0)
            title = pg.get("title", "")
            low = title.lower()
            if ii.get("mime") != "image/jpeg" or not h or w < 1400 or not (1.25 <= w / h <= 2.3):
                continue
            if any(k in low for k in ART_SKIP):
                continue
            cands.append((title, ii))
        random.shuffle(cands)
        got = 0
        for title, ii in cands:
            if got >= ART_PER_PAINTER:
                break
            name = hashlib.md5(title.encode()).hexdigest()[:12] + ".jpg"
            if name in seen:
                continue
            try:
                img = web_get(ii.get("thumburl") or ii["url"], timeout=60)
                if len(img) < 50000:
                    continue
                with open(os.path.join(ART_DIR, name), "wb") as f:
                    f.write(img)
            except Exception:
                continue
            md = ii.get("extmetadata", {})
            label = clean_html(md.get("ObjectName", {}).get("value")) or title[5:].rsplit(".", 1)[0]
            items.append({"file": name, "title": label[:90],
                          "artist": clean_html(md.get("Artist", {}).get("value"))[:60] or cat[13:],
                          "date": clean_html(md.get("DateTimeOriginal", {}).get("value"))[:20]})
            seen.add(name)
            got += 1
            time.sleep(1)
        with open(os.path.join(ART_DIR, "index.json"), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
    log(f"Tableaux : {len(items)} paysages dans la bibliothèque")


def art_loop():
    for attempt in range(6):
        try:
            with art_lock:
                build_art()
            if len(art_list()) >= 20:
                return
        except Exception as e:
            log(f"Tableaux : {e}")
        time.sleep(600)


# ------------------------------------------------------------ night screen
SCREEN_TIMEOUT = int(os.environ.get("SCREEN_TIMEOUT", "0") or 0)
NIGHT_FROM = os.environ.get("SCREEN_OFF_FROM", "").strip()
NIGHT_TO = os.environ.get("SCREEN_OFF_TO", "").strip()
NIGHT_IDLE_S = 60  # at night a touch wakes the screen up for 1 minute


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_night():
    if not NIGHT_FROM or not NIGHT_TO:
        return False
    now = time.localtime()
    n, a, b = now.tm_hour * 60 + now.tm_min, minutes(NIGHT_FROM), minutes(NIGHT_TO)
    return a <= n < b if a <= b else (n >= a or n < b)


def night_loop():
    if not NIGHT_FROM or not NIGHT_TO:
        return
    try:
        minutes(NIGHT_FROM), minutes(NIGHT_TO)
    except ValueError:
        log(f"Horaires d'extinction invalides : {NIGHT_FROM} / {NIGHT_TO}")
        return
    log(f"Écran éteint chaque nuit de {NIGHT_FROM} à {NIGHT_TO}")
    state = None
    while True:
        night = is_night()
        if night != state:
            if night:
                xset("s", str(NIGHT_IDLE_S))
                xset("+dpms")
                xset("dpms", str(NIGHT_IDLE_S), str(NIGHT_IDLE_S), str(NIGHT_IDLE_S))
                xset("dpms", "force", "off")
                log("Nuit : écran éteint (un toucher le rallume 1 minute)")
            elif state is not None:
                if SCREEN_TIMEOUT:
                    t = str(SCREEN_TIMEOUT)
                    xset("s", t)
                    xset("dpms", t, t, t)
                else:
                    xset("s", "off")
                    xset("-dpms")
                xset("dpms", "force", "on")
                log("Matin : écran rallumé")
            state = night
        elif night:
            # Chrome can keep the screen awake while the camera plays: switch it
            # off again once nobody has touched it for a minute
            try:
                idle_ms = (with_page(lambda p, t: p.eval(IDLE_JS)) or [0])[0]
                if idle_ms >= NIGHT_IDLE_S * 1000:
                    xset("dpms", "force", "off")
            except Exception:
                pass
        time.sleep(20)


# --------------------------------------------------------------- local API
def xset(*args):
    env = dict(os.environ, DISPLAY=":0")
    subprocess.run(["xset", *args], env=env, check=False)


class Api(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except Exception:
            return {}

    def do_GET(self):
        if self.path.startswith("/art/"):
            name = os.path.basename(self.path[5:])
            path = os.path.join(ART_DIR, name)
            if not name.endswith(".jpg") or not os.path.isfile(path):
                return self.reply(404, {"error": "inconnu"})
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/status":
            t = page_target()
            return self.reply(200, {"url": t.get("url") if t else None, "home": HOME_URL})
        self.reply(404, {"error": "inconnu"})

    def do_POST(self):
        try:
            if self.path == "/reload":
                with_page(lambda p, t: p.call("Page.reload", ignoreCache=True))
            elif self.path == "/home":
                with_page(lambda p, t: p.goto(HOME_URL, settle=0))
            elif self.path == "/navigate":
                d = self.body()
                path = str(d.get("path") or "")
                if not path.startswith("/"):
                    return self.reply(400, {"error": "path doit commencer par /"})
                with_page(lambda p, t: p.goto(HA + path, settle=0))
            elif self.path == "/screen/on":
                xset("dpms", "force", "on")
                xset("s", "reset")
            elif self.path == "/screen/off":
                xset("dpms", "force", "off")
            elif self.path == "/login":
                ensure_session(force_login=True)
            else:
                return self.reply(404, {"error": "inconnu"})
            self.reply(200, {"ok": True})
        except Exception as e:
            self.reply(500, {"error": str(e)})


def main():
    log("Attente de Home Assistant...")
    wait_for(f"{HA}/manifest.json", "Home Assistant")
    log("Attente de Chrome...")
    wait_for(f"{CDP}/json/version", "Chrome", timeout=120)
    for attempt in range(1, 6):
        try:
            ensure_session()
            break
        except Exception as e:
            log(f"Ouverture de session impossible (essai {attempt}/5) : {e}")
            time.sleep(10)
    log_gpu()
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=helpers_loop, daemon=True).start()
    threading.Thread(target=night_loop, daemon=True).start()
    threading.Thread(target=art_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", API_PORT), Api)
    log(f"API locale : http://127.0.0.1:{API_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
