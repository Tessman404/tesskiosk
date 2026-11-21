################################################################################
# TessKiosk — Home Assistant dashboard in kiosk mode with Chromium
# X server start-up logic adapted from HAOS Kiosk Display (Jeff Kosowsky, GPL-2.0)
################################################################################
ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:trixie
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ENV ADDON_VERSION=${BUILD_VERSION} \
    DISPLAY=:0 \
    LANG=fr_FR.UTF-8 \
    TZ=Europe/Paris

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      xserver-xorg-core \
      xserver-xorg-input-libinput \
      x11-xserver-utils \
      xinput \
      xdotool \
      openbox \
      unclutter-xfixes \
      udev \
      libinput-tools \
      dbus \
      dbus-x11 \
      libgl1-mesa-dri \
      mesa-va-drivers \
      intel-media-va-driver \
      i965-va-driver \
      vainfo \
      chromium \
      fonts-dejavu-core \
      fonts-noto-color-emoji \
      locales \
      procps \
      python3 \
      python3-websocket \
 && sed -i 's/^# *fr_FR.UTF-8/fr_FR.UTF-8/' /etc/locale.gen && locale-gen \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY xorg.conf /etc/X11/xorg.conf.tesskiosk
COPY run.sh kiosk.py /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
