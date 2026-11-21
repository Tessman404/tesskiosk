"""TessKiosk — Bluetooth pairing of mice / keyboards / touch devices.

Talks to the host's BlueZ through the system D-Bus (host_dbus) using
bluetoothctl in a single interactive session (so the pairing agent stays
registered while pairing).
"""
import queue
import re
import subprocess
import sys
import threading
import time

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x01|\x02|\r")
HID_ICONS = ("input-mouse", "input-keyboard", "input-tablet", "input-gaming")
HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"   # classic HID
HOGP_UUID = "00001812-0000-1000-8000-00805f9b34fb"  # BLE HID (HoG)


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + "Bluetooth : " + msg, flush=True)


class Ctl:
    def __init__(self):
        self.p = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.q = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.q.put(ANSI.sub("", line).strip())

    def send(self, cmd):
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()

    def collect(self, seconds, until=None):
        out, end = [], time.time() + seconds
        while time.time() < end:
            try:
                line = self.q.get(timeout=0.3)
            except queue.Empty:
                continue
            out.append(line)
            if until and any(u in line for u in until):
                break
        return out

    def run(self, cmd, seconds=4, until=None):
        self.send(cmd)
        return self.collect(seconds, until)

    def close(self):
        try:
            self.send("quit")
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def is_input_device(info_lines):
    text = "\n".join(info_lines)
    return any(i in text for i in HID_ICONS) or HID_UUID in text or HOGP_UUID in text


def pair(scan_seconds=45):
    ctl = Ctl()
    try:
        ctl.run("agent NoInputNoOutput", 2)
        ctl.run("default-agent", 2)
        ctl.run("power on", 2)
        log(f"recherche d'appareils pendant {scan_seconds} s (mets la souris en mode appairage)...")
        ctl.send("scan on")
        lines = ctl.collect(scan_seconds)
        macs = []
        for line in lines + ctl.run("devices", 3):
            m = re.search(r"Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2})", line)
            if m and m.group(1) not in macs:
                macs.append(m.group(1))
        done = 0
        for mac in macs:
            info = ctl.run(f"info {mac}", 3)
            if not is_input_device(info):
                continue
            name = next((l.split(":", 1)[1].strip() for l in info if l.startswith("Name:")), mac)
            if any("Paired: yes" in l for l in info):
                log(f"« {name} » déjà appairé")
                ctl.run(f"trust {mac}", 2)
                ctl.run(f"connect {mac}", 10, until=("Connection successful", "Failed"))
                done += 1
                continue
            log(f"appairage de « {name} » ({mac})...")
            res = ctl.run(f"pair {mac}", 25, until=("Pairing successful", "Failed to pair", "AlreadyExists"))
            ok = any("Pairing successful" in l or "AlreadyExists" in l for l in res)
            ctl.run(f"trust {mac}", 3)
            con = ctl.run(f"connect {mac}", 15, until=("Connection successful", "Failed to connect"))
            if ok or any("Connection successful" in l for l in con):
                log(f"« {name} » appairé et connecté ✔")
                done += 1
            else:
                log(f"échec avec « {name} » : " + " | ".join(l for l in res + con if "Fail" in l)[:200])
        ctl.run("scan off", 2)
        if not done:
            log("aucune souris ou clavier trouvé. Remets l'appareil en mode appairage et relance.")
        return done
    finally:
        ctl.close()


if __name__ == "__main__":
    sys.exit(0 if pair(int(sys.argv[1]) if len(sys.argv) > 1 else 45) else 1)
