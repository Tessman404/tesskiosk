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
def page_target():
    try:
        _, targets = http("GET", f"{CDP}/json", timeout=3)
    except Exception:
        return None
    pages = [t for t in targets if t.get("type") == "page"
             and not t.get("url", "").startswith(("chrome://", "devtools://", "chrome-extension://"))]
    return pages[0] if pages else None


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
        p.goto(HOME_URL)
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
            # one quiet reload per night to keep memory low
            now = time.localtime()
            if now.tm_hour == 4 and last_reload_day != now.tm_yday:
                last_reload_day = now.tm_yday
                with_page(lambda p, t: p.call("Page.reload", ignoreCache=False))
                log("Rechargement nocturne")
        except Exception as e:  # keep watching whatever happens
            log(f"Surveillance : {e}")


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
    server = ThreadingHTTPServer(("127.0.0.1", API_PORT), Api)
    log(f"API locale : http://127.0.0.1:{API_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
