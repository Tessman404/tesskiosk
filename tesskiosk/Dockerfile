################################################################################
# TessKiosk — Home Assistant dashboard in kiosk mode with Chromium
# X server / input set-up adapted from HAOS Kiosk Display (Jeff Kosowsky, GPL-2.0)
################################################################################
ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ENV ADDON_VERSION=${BUILD_VERSION} \
    DISPLAY=:0 \
    TZ=Europe/Paris

RUN apk update && apk add --no-cache \
      xorg-server \
      xf86-video-modesetting \
      xf86-input-libinput \
      libinput \
      libinput-udev \
      udev \
      libevdev \
      mesa-dri-gallium \
      mesa-egl \
      mesa-gles \
      mesa-va-gallium \
      libdrm \
      intel-media-driver \
      libva-intel-driver \
      libva-utils \
      xinput \
      xrandr \
      xset \
      xdotool \
      setxkbmap \
      openbox \
      unclutter-xfixes \
      dbus \
      dbus-x11 \
      bluez \
      chromium \
      ttf-dejavu \
      font-roboto \
      font-liberation \
      font-noto-emoji \
      icu-data-full \
      bash \
      util-linux \
      python3 \
      py3-websocket-client \
 && rm -rf /var/cache/apk/*

COPY xorg.conf /etc/X11/xorg.conf.tesskiosk
COPY fonts.conf /etc/fonts/local.conf
COPY run.sh kiosk.py bt.py keyboard.js /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
