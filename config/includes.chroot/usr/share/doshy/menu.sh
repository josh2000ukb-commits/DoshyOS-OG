#!/bin/sh
# Emits the doshy OS start menu in jgmenu CSV format: name,command,icon

echo "Files,thunar,system-file-manager"
echo "Terminal,xfce4-terminal,org.xfce.terminal"
echo "Firefox,firefox-esr,firefox-esr"
if command -v discord >/dev/null 2>&1; then
    echo "Discord,discord,discord"
fi

echo "^sep()"
# Every installed program (including .deb packages you install yourself),
# generated fresh each time the menu opens
echo "Applications,^pipe(/usr/share/doshy/apps.sh),applications-other"
echo "Settings,^checkout(settings),preferences-system"

# Only offer the installer while running from the live USB/DVD
# (lets you do a full install onto another drive, e.g. a second external SSD)
if [ -d /run/live/medium ] && command -v calamares >/dev/null 2>&1; then
    echo "^sep()"
    echo "Install doshy OS to a drive,doshy-install,drive-harddisk"
fi

echo "^sep()"
echo "Switch user,dm-tool switch-to-greeter,system-switch-user"
echo "Log out,openbox --exit,system-log-out"
echo "Restart,systemctl reboot,system-reboot"
echo "Shut down,systemctl poweroff,system-shutdown"

# --- Settings submenu ---
echo "^tag(settings)"
echo "Keyboard language,doshy-keyboard,input-keyboard"
echo "Display and monitors,doshy-display,video-display"
echo "Wallpaper,doshy-wallpaper,preferences-desktop-wallpaper"
echo "Sound,pavucontrol,audio-volume-high"
echo "Network,nm-connection-editor,network-wireless"
echo "Users,doshy-users,system-users"
echo "System updates,doshy-update,system-software-update"
echo "^sep()"
echo "Install a package (.deb),doshy-install-deb,system-software-install"
echo "^sep()"
echo "All settings,doshy-settings,preferences-system"
