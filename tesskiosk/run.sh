#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
################################################################################
# TessKiosk — start X, then Chromium in kiosk mode, then the session manager.
# X start-up logic adapted from HAOS Kiosk Display (Jeff Kosowsky, GPL-2.0).
################################################################################
set -uo pipefail

bashio::log.info "######## TessKiosk ${ADDON_VERSION:-dev} ########"

TTY0_DELETED=""
cleanup() {
    bashio::log.info "Arrêt de TessKiosk..."
    jobs -p | xargs -r kill 2>/dev/null
    [ -n "$TTY0_DELETED" ] && [ ! -e /dev/tty0 ] && mknod -m 620 /dev/tty0 c 4 0
    exit 0
}
trap cleanup HUP INT QUIT TERM

#------------------------------------------------------------------- options --
export HA_USERNAME="$(bashio::config 'ha_username')"
export HA_PASSWORD="$(bashio::config 'ha_password')"
export HA_URL="$(bashio::config 'ha_url')"
export HA_URL="${HA_URL%/}"
export DASHBOARD="$(bashio::config 'dashboard')"
export API_PORT="$(bashio::config 'api_port')"
ZOOM="$(bashio::config 'zoom')"
SCREEN_TIMEOUT="$(bashio::config 'screen_timeout')"
ROTATE="$(bashio::config 'rotate')"
HIDE_CURSOR="$(bashio::config 'hide_cursor')"
HW_VIDEO="$(bashio::config 'hardware_video')"
BT_PAIRING="$(bashio::config 'bluetooth_pairing')"

if [ -z "$HA_USERNAME" ] || [ -z "$HA_PASSWORD" ]; then
    bashio::log.fatal "Renseigne ha_username et ha_password dans la configuration."
    exit 1
fi
bashio::log.info "Utilisateur=$HA_USERNAME  URL=$HA_URL  Tableau=$DASHBOARD  Zoom=$ZOOM%"

#---------------------------------------------------------------------- dbus --
mkdir -p /run/dbus
dbus-daemon --system --fork 2>/dev/null || true
DBUS_SESSION_BUS_ADDRESS=$(dbus-daemon --session --fork --print-address 2>/dev/null || true)
export DBUS_SESSION_BUS_ADDRESS

#---------------------------------------------------------------------- udev --
UDEVD=""
for c in /usr/lib/systemd/systemd-udevd /lib/systemd/systemd-udevd "$(command -v udevd 2>/dev/null)"; do
    [ -n "$c" ] && [ -x "$c" ] && UDEVD="$c" && break
done
if [ -n "$UDEVD" ]; then
    if "$UDEVD" --daemon; then
        udevadm trigger >/dev/null 2>&1 || true   # some sysfs entries are read-only: harmless
        udevadm settle --timeout=10 || true
    else
        bashio::log.warning "udev n'a pas démarré (tactile/souris possiblement absents)"
    fi
else
    bashio::log.warning "udevd introuvable"
fi
# udev cannot "trigger" in a container (read-only sysfs): process each input device by hand
for dev in /dev/input/event*; do
    [ -c "$dev" ] || continue
    devpath=$(udevadm info --query=path --name="$dev" 2>/dev/null) || continue
    udevadm test "$devpath" >/dev/null 2>&1 || true
done
udevadm settle --timeout=10 || true
bashio::log.info "Périphériques d'entrée :"
awk -F'"' '/^N: Name=/{print "  - " $2}' /proc/bus/input/devices
ls /dev/input/ 2>/dev/null | tr '\n' ' ' | sed 's/^/  nœuds : /'; echo

#------------------------------------------------------------- video output --
selected_card=""
for status_path in /sys/class/drm/card[0-9]*-*/status; do
    [ -e "$status_path" ] || continue
    status=$(cat "$status_path")
    card_port=$(basename "$(dirname "$status_path")")
    card=${card_port%%-*}
    driver=$(basename "$(readlink "/sys/class/drm/$card/device/driver")")
    bashio::log.info "  $card_port ($driver) : $status"
    if [ -z "$selected_card" ] && [ "$status" = "connected" ]; then
        selected_card="$card"
    fi
done
if [ -z "$selected_card" ]; then
    bashio::log.fatal "Aucun écran branché. Branche un écran (HDMI) puis redémarre l'extension."
    exit 1
fi

#------------------------------------------------------------------- X start --
rm -rf /tmp/.X*-lock /tmp/.X11-unix
cp /etc/X11/xorg.conf.tesskiosk /etc/X11/xorg.conf
sed -i "/Option[[:space:]]\+\"DRI\"[[:space:]]\+\"3\"/a\    Option \"kmsdev\" \"/dev/dri/$selected_card\"" /etc/X11/xorg.conf

# Xorg would grab the host console through /dev/tty0: hide it during start-up.
if [ -e /dev/tty0 ]; then
    if mount -o remount,rw /dev && rm -f /dev/tty0; then
        TTY0_DELETED=1
    else
        bashio::log.warning "Impossible de masquer /dev/tty0"
    fi
fi

bashio::log.info "Démarrage de l'affichage (X)..."
Xorg -nolisten tcp </dev/null >/tmp/xorg.out 2>&1 &
for i in $(seq 1 30); do
    xset q >/dev/null 2>&1 && break
    sleep 1
done
if [ -n "$TTY0_DELETED" ]; then
    mknod -m 620 /dev/tty0 c 4 0 && TTY0_DELETED=""
fi
if ! xset q >/dev/null 2>&1; then
    bashio::log.fatal "L'affichage n'a pas démarré :"
    tail -n 40 /tmp/xorg.out
    exit 1
fi
bashio::log.info "Affichage démarré en ${i}s"

openbox >/dev/null 2>&1 &
sleep 0.5

# Screen output, rotation, size
OUTPUT=$(xrandr --query | awk '/ connected/{print $1; exit}')
for o in $(xrandr --query | awk '/ connected/{print $1}'); do
    [ "$o" = "$OUTPUT" ] || xrandr --output "$o" --off
done
xrandr --output "$OUTPUT" --primary --auto --rotate "$ROTATE"
read -r SCREEN_W SCREEN_H < <(xrandr --query | awk '/ connected primary/{ if (match($0,/[0-9]+x[0-9]+\+/)) { s=substr($0,RSTART,RLENGTH-1); split(s,a,"x"); print a[1], a[2] } }')
SCREEN_W=${SCREEN_W:-1920}; SCREEN_H=${SCREEN_H:-1080}
bashio::log.info "Écran : $OUTPUT ${SCREEN_W}x${SCREEN_H} (rotation $ROTATE)"

bashio::log.info "Entrées vues par l'affichage :"
xinput list --name-only 2>/dev/null | grep -v -i "virtual core\|xtest" | sed 's/^/  - /'

# Map touch screens onto the output
while IFS= read -r id; do
    name=$(xinput list --name-only "$id" 2>/dev/null)
    [[ "${name,,}" =~ touch|stylus ]] || continue
    xinput list-props "$id" 2>/dev/null | grep -q "Coordinate Transformation Matrix" || continue
    xinput map-to-output "$id" "$OUTPUT" && bashio::log.info "Tactile « $name » associé à $OUTPUT"
done < <(xinput list --id-only 2>/dev/null)

# Screen saver / power saving
if [ "$SCREEN_TIMEOUT" -eq 0 ]; then
    xset s off; xset s noblank; xset -dpms
else
    xset s "$SCREEN_TIMEOUT"; xset +dpms; xset dpms "$SCREEN_TIMEOUT" "$SCREEN_TIMEOUT" "$SCREEN_TIMEOUT"
fi
if [ "$HIDE_CURSOR" = "true" ]; then
    UNCL=$(command -v unclutter-xfixes || command -v unclutter || true)
    [ -n "$UNCL" ] && "$UNCL" --timeout 2 --hide-on-touch --start-hidden --fork 2>/dev/null
fi

#---------------------------------------------------------- hardware video --
if [ "$HW_VIDEO" = "true" ]; then
    for drv in iHD i965 radeonsi; do
        if LIBVA_DRIVER_NAME=$drv vainfo >/dev/null 2>&1; then
            export LIBVA_DRIVER_NAME=$drv
            bashio::log.info "Décodage vidéo matériel : pilote $drv"
            break
        fi
    done
    [ -z "${LIBVA_DRIVER_NAME:-}" ] && bashio::log.warning "Décodage vidéo matériel indisponible, décodage logiciel"
fi

#------------------------------------------------------------------ Chromium --
SCALE=$(awk -v z="$ZOOM" 'BEGIN{printf "%.2f", z/100}')
PROFILE=/data/chromium
mkdir -p "$PROFILE"
rm -f "$PROFILE"/Singleton*
# Pretend the last session closed cleanly (no "restore pages" bubble)
if [ -f "$PROFILE/Default/Preferences" ]; then
    sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/; s/"exited_cleanly":false/"exited_cleanly":true/' "$PROFILE/Default/Preferences"
fi

CHROME_FLAGS=(
    --no-sandbox --test-type
    --user-data-dir="$PROFILE"
    --disk-cache-dir=/tmp/chromium-cache
    --kiosk --start-fullscreen
    --window-position=0,0 --window-size="${SCREEN_W},${SCREEN_H}"
    --force-device-scale-factor="$SCALE"
    --no-first-run --no-default-browser-check
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble
    --disable-features=Translate,TranslateUI,MediaRouter,HardwareMediaKeyHandling
    --password-store=basic
    --disable-dev-shm-usage
    --autoplay-policy=no-user-gesture-required
    --overscroll-history-navigation=0 --disable-pinch
    --touch-events=enabled
    --lang=fr --accept-lang=fr-FR,fr
    --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222
    --remote-allow-origins=http://127.0.0.1:9222
    --ignore-gpu-blocklist --enable-gpu-rasterization --enable-zero-copy
)
if [ -n "${LIBVA_DRIVER_NAME:-}" ]; then
    CHROME_FLAGS+=(--enable-features=VaapiVideoDecoder,VaapiVideoDecodeLinuxGL,VaapiIgnoreDriverChecks,AcceleratedVideoDecodeLinuxGL)
fi

START_URL="$HA_URL/manifest.json"

chrome_loop() {
    while true; do
        bashio::log.info "Lancement de Chrome..."
        chromium "${CHROME_FLAGS[@]}" "$START_URL" >/tmp/chromium.log 2>&1
        bashio::log.warning "Chrome s'est arrêté (code $?), relance dans 3 s"
        tail -n 15 /tmp/chromium.log
        sleep 3
    done
}
chrome_loop &

#----------------------------------------------------------------- bluetooth --
if [ "$BT_PAIRING" = "true" ]; then
    bashio::log.info "Mode appairage Bluetooth activé (désactive-le ensuite dans la configuration)"
    python3 -u /bt.py 60 &
fi

#---------------------------------------------------- session manager (py) --
bashio::log.info "Démarrage du gestionnaire de session..."
python3 -u /kiosk.py &
KIOSK_PID=$!
wait "$KIOSK_PID"
bashio::log.error "Le gestionnaire de session s'est arrêté, redémarrage de l'extension"
exit 1
