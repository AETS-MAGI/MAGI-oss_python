#!/bin/sh
# xrdp GNOME session fix
# Copy to ~/.xsessionrc
# See: https://github.com/neutrinolabs/xrdp/issues/2060

export GNOME_SHELL_SESSION_MODE=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export XDG_CONFIG_DIRS=/etc/xdg/xdg-ubuntu:/etc/xdg
