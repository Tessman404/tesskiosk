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
import json
import os
import shutil
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


AWAY_PERSONS = [x.strip() for x in os.environ.get("AWAY_PERSONS", "").split(",")
                if x.strip() and x.strip() != "null"]
AWAY_IDLE_S = int(os.environ.get("AWAY_IDLE_MINUTES", "5") or 5) * 60
PRESENCE_JS = """(() => { const h = document.querySelector('home-assistant');
  const st = h && h.hass && h.hass.states; if (!st) return null;
  const ids = %s; const known = ids.filter(e => st[e]);
  if (!known.length) return null;
  return known.some(e => st[e].state === 'home'); })()"""


def page_idle_ms():
    try:
        return (with_page(lambda p, t: p.eval(IDLE_JS)) or [0])[0]
    except Exception:
        return 0


def someone_home():
    """True / False from the person entities, None if unknown."""
    if not AWAY_PERSONS:
        return None
    try:
        return with_page(lambda p, t: p.eval(PRESENCE_JS % json.dumps(AWAY_PERSONS)))
    except Exception:
        return None


def screen_normal():
    if SCREEN_TIMEOUT:
        t = str(SCREEN_TIMEOUT)
        xset("s", t)
        xset("dpms", t, t, t)
    else:
        xset("s", "off")
        xset("-dpms")


def night_loop():
    """Screen off at night, and when nobody (Thomas, Alexia...) is at home.
    A touch wakes it up; it goes back to sleep after a while without touch."""
    night_ok = bool(NIGHT_FROM and NIGHT_TO)
    if night_ok:
        try:
            minutes(NIGHT_FROM), minutes(NIGHT_TO)
        except ValueError:
            log(f"Horaires d'extinction invalides : {NIGHT_FROM} / {NIGHT_TO}")
            night_ok = False
    if night_ok:
        log(f"Écran éteint chaque nuit de {NIGHT_FROM} à {NIGHT_TO}")
    if AWAY_PERSONS:
        log(f"Écran éteint quand personne n'est à la maison ({', '.join(AWAY_PERSONS)}), "
            f"rallumé au retour ; un toucher le rallume {AWAY_IDLE_S // 60} min")
    if not night_ok and not AWAY_PERSONS:
        return
    night_state, home_state = None, None
    while True:
        night = night_ok and is_night()
        home = someone_home()
        away = home is False
        # --- night
        if night != night_state:
            if night:
                xset("s", str(NIGHT_IDLE_S))
                xset("+dpms")
                xset("dpms", str(NIGHT_IDLE_S), str(NIGHT_IDLE_S), str(NIGHT_IDLE_S))
                xset("dpms", "force", "off")
                log("Nuit : écran éteint (un toucher le rallume 1 minute)")
            elif night_state is not None:
                screen_normal()
                if not away:
                    xset("dpms", "force", "on")
                    log("Matin : écran rallumé")
            night_state = night
        # --- presence
        if home is not None and home != home_state:
            if away and not night:
                log("Personne à la maison : écran éteint")
                xset("dpms", "force", "off")
            elif home and home_state is False and not night:
                xset("dpms", "force", "on")
                log("Retour à la maison : écran rallumé")
            home_state = home
        # --- back to sleep after a touch (Chrome can keep the screen awake while
        # the camera plays, so it is switched off again explicitly)
        limit = NIGHT_IDLE_S if night else (AWAY_IDLE_S if away else None)
        if limit and page_idle_ms() >= limit * 1000:
            xset("dpms", "force", "off")
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
    shutil.rmtree("/data/art", ignore_errors=True)  # ancienne bibliothèque (0.7.0)
    server = ThreadingHTTPServer(("127.0.0.1", API_PORT), Api)
    log(f"API locale : http://127.0.0.1:{API_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
