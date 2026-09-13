#!/bin/bash
# Runs on Linux/WSL using only shell tools; never builds or changes host accounts.
set -euo pipefail
SOURCE=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/source" "$WORK/bin" "$WORK/home"
cp -R "$SOURCE/auto" "$SOURCE/config" "$WORK/source/"
find "$WORK/source" -type f ! -name '*.png' -exec sed -i 's/\r$//' {} +
ROOT="$WORK/source"
BIN="$ROOT/config/includes.chroot/usr/local/bin"
export HOME="$WORK/home" LOG="$WORK/calls" PATH="$WORK/bin:$PATH"
export XDG_CONFIG_HOME="$HOME/.config" XDG_DATA_HOME="$HOME/.local/share"
pass() { printf 'PASS: %s\n' "$1"; }
mock() { printf '#!/bin/sh\n%s\n' "$2" > "$WORK/bin/$1"; chmod +x "$WORK/bin/$1"; }

# Syntax validation includes hooks and all extensionless executables.
while IFS= read -r f; do
    case "$(head -n1 "$f")" in
        '#!/bin/sh') sh -n "$f" ;;
        '#!/bin/bash') bash -n "$f" ;;
    esac
done < <(find "$ROOT" -type f ! -name '*.png')
bash -n "$SOURCE/build.sh"
bash -n "$SOURCE/scripts/docker-build.sh"
pass 'shell syntax'

mock lb 'echo simulated-build-failure; exit 37'
set +e
(cd "$WORK"; bash "$ROOT/auto/build") > "$WORK/build-output" 2>&1
status=$?
set -e
[ "$status" = 37 ]
grep -q simulated-build-failure "$WORK/build.log"
pass 'build failure survives tee and keeps its log'

mock id 'case "$1" in -un) echo tester ;; -nG) echo "tester sudo" ;; *) echo 1000 ;; esac'
mock zenity 'printf "<%s>\n" "$@" >> "$LOG"; exit 1'
: > "$LOG"
sh "$BIN/doshy-users"
grep -qx '<--extra-button=Add user>' "$LOG"
grep -qx '<--extra-button=Edit user>' "$LOG"
grep -qx '<--extra-button=Remove user>' "$LOG"
pass 'Users button labels remain single arguments'

mock id 'case "$1" in -un) echo tester ;; -nG) echo tester ;; *) echo 1000 ;; esac'
mock zenity 'if [ ! -f "$HOME/chosen" ]; then touch "$HOME/chosen"; echo "Change my password"; fi; exit 1'
mock xfce4-terminal 'printf "%s\n" "$@" >> "$LOG"'
mock sudo 'echo UNEXPECTED-SUDO >> "$LOG"; exit 1'
mock pkexec 'echo UNEXPECTED-PKEXEC >> "$LOG"; exit 1'
: > "$LOG"
sh "$BIN/doshy-users"
grep -qx passwd "$LOG"
! grep -q UNEXPECTED "$LOG"
pass 'standard users change their own password without administrator elevation'

mock xrandr 'if [ "$1" = --query ]; then cat "$HOME/xrandr"; else printf "%s\n" "$*" >> "$LOG"; fi'
cat > "$HOME/xrandr" <<'EOF'
HDMI-1 connected primary 1280x720+0+0 (normal)
   1280x720 60.00*
   1920x1080 60.00+
DP-1 connected 1280x1024+1280+0 (normal)
   1280x1024 60.00*+
EOF
mock zenity 'case "$*" in *--list*) echo "Place screens side by side" ;; esac'
mock pgrep 'exit 1'
for command in xsetroot openbox tint2 doshy-wallpaper sleep; do mock "$command" 'exit 0'; done
: > "$LOG"
sh "$BIN/doshy-display"
grep -qx -- '--output HDMI-1 --mode 1920x1080 --primary --pos 0x0' "$LOG"
grep -qx -- '--output DP-1 --mode 1280x1024 --pos 1920x0' "$LOG"
! grep -q -- '--fb 0x0' "$LOG"
pass 'preferred display mode is singular and screen offsets match selected modes'

mock xrandr 'if [ "$1" = --query ]; then cat "$HOME/xrandr"; else exit 1; fi'
if sh "$BIN/doshy-display"; then echo 'display failure was ignored' >&2; exit 1; fi
pass 'failed display changes return failure'

mock setxkbmap 'printf "<%s>\n" "$@" >> "$LOG"'
mkdir -p "$XDG_CONFIG_HOME/doshy"
printf 'LAYOUT=gb\nVARIANT=\n' > "$XDG_CONFIG_HOME/doshy/keyboard"
: > "$LOG"
sh "$BIN/doshy-keyboard" --restore
grep -A1 -x '<-variant>' "$LOG" | grep -qx '<>'
pass 'switching keyboard layouts clears the previous variant'

# Exercise account helper with all mutations mocked, including root detection.
mock id 'case "$1" in -u) if [ "$#" = 1 ]; then echo 0; else echo 1000; fi ;; -nG) echo tester ;; esac'
mock getent 'case "$1" in passwd) echo "tester:x:1000:1000:Tester:/home/tester:/bin/sh" ;; group) exit 2 ;; esac'
mock usermod 'printf "%s\n" "$*" >> "$LOG"'
mock gpasswd 'exit 0'
mock chpasswd 'cat >> "$LOG"'
mock shred 'rm -f -- "$2"'
printf 'tester:abc:def:ghi\n' > "$WORK/password"
: > "$LOG"
sh "$BIN/doshy-users-helper" edit tester tester Tester standard "$WORK/password"
grep -qx 'tester:abc:def:ghi' "$LOG"
pass 'account edit handles absent optional groups and preserves password colons'

printf 'other:secret\n' > "$WORK/password"
: > "$LOG"
if sh "$BIN/doshy-users-helper" passwd tester "$WORK/password" 2>/dev/null; then exit 1; fi
[ ! -s "$LOG" ]
printf 'tester:secret\nother:secret\n' > "$WORK/password"
if sh "$BIN/doshy-users-helper" passwd tester "$WORK/password" 2>/dev/null; then exit 1; fi
[ ! -s "$LOG" ]
pass 'mismatched and multiple password records are rejected before mutation'

mock pgrep 'exit 0'
: > "$LOG"
if sh "$BIN/doshy-users-helper" edit tester renamed Tester standard - 2>/dev/null; then exit 1; fi
[ ! -s "$LOG" ]
pass 'active accounts cannot be renamed'

# Application fixtures include characters that must survive shell quoting.
mkdir -p "$XDG_DATA_HOME/applications" "$WORK/system-apps"
APP="$XDG_DATA_HOME/applications/quote' and, comma.desktop"
cat > "$APP" <<'EOF'
[Desktop Entry]
Type=Application
Name=Fixture, App
Exec=example --literal=a,b %c %k %f
EOF
cat > "$WORK/system-apps/override.desktop" <<'EOF'
[Desktop Entry]
Name=Must Be Hidden
Exec=example
EOF
printf '[Desktop Entry]\nHidden=true\n' > "$XDG_DATA_HOME/applications/override.desktop"
APPS="$ROOT/config/includes.chroot/usr/share/doshy/apps.sh"
sed -i "s|/usr/share/applications|$WORK/system-apps|g; s|/usr/local/share/applications|$WORK/empty-apps|g" "$APPS"
mock gio 'printf "%s\n" "$2" > "$LOG"'
sh "$APPS" --rows > "$WORK/rows"
! grep -q 'Must Be Hidden' "$WORK/rows"
cmd=$(sed -n '2p' "$WORK/rows")
sh -c "$cmd"
[ "$(cat "$LOG")" = "$APP" ]
sh "$APPS" | grep -q '^"""Fixture, App""",'
pass 'Applications honours hidden overrides and preserves quoted paths and commas'

mock xrandr 'cat "$HOME/xrandr"'
printf 'HDMI-1 connected 1920x1080+0+0\nDP-1 connected (normal)\n' > "$HOME/xrandr"
touch "$HOME/old.png" "$HOME/new.png"
printf '%s\n' "$HOME/old.png" > "$XDG_CONFIG_HOME/doshy/wallpaper"
mock xwallpaper 'printf "%s\n" "$*" > "$LOG"; exit 1'
if sh "$BIN/doshy-wallpaper" "$HOME/new.png"; then exit 1; fi
[ "$(cat "$XDG_CONFIG_HOME/doshy/wallpaper")" = "$HOME/old.png" ]
! grep -q DP-1 "$LOG"
pass 'wallpaper ignores disabled outputs and preserves saved choice on failure'

# Redirect every absolute state path in login-setup into a private fixture.
LOGIN="$ROOT/config/includes.chroot/usr/local/lib/doshy/login-setup"
mkdir -p "$WORK/live/medium" "$WORK/lightdm/lightdm.conf.d"
sed -i "s|/run/live|$WORK/live|g; s|/etc/lightdm|$WORK/lightdm|g; s|/proc/cmdline|$WORK/cmdline|g" "$LOGIN"
mock getent 'if [ "$2" = doshy ]; then echo "doshy:x:1000:1000::/home/doshy:/bin/sh"; else exit 2; fi'
mock passwd 'if [ "$1" = -S ]; then printf "doshy %s\n" "$(cat "$HOME/password-state")"; fi'
mock chpasswd 'cat >> "$LOG"'
echo 'boot=live username=doshy' > "$WORK/cmdline"
echo NP > "$HOME/password-state"
: > "$LOG"
sh "$LOGIN"
grep -qx doshy:live "$LOG"
grep -qx autologin-user=doshy "$WORK/lightdm/lightdm.conf.d/99-autologin.conf"
pass 'automatic login still initializes a blank live password'

echo 'boot=live username=doshy noautologin' > "$WORK/cmdline"
echo L > "$HOME/password-state"
: > "$LOG"
sh "$LOGIN"
[ ! -s "$LOG" ]
[ ! -f "$WORK/lightdm/lightdm.conf.d/99-autologin.conf" ]
pass 'normal boot disables persisted autologin and preserves locked accounts'

mkdir -p "$WORK/downloads"
touch "$WORK/downloads/example.deb"
mock dpkg-deb 'case "$3" in Package) echo fixture ;; Version) echo 1 ;; Architecture) echo amd64 ;; Description) echo Fixture ;; esac'
mock dpkg 'exit 1'
mock zenity 'exit 0'
mock sudo 'shift; if [ "$1" = true ]; then exit 0; fi; exec "$@"'
mock apt-get 'printf "<%s>\n" "$@" >> "$LOG"; exit 0'
: > "$LOG"
sh "$BIN/doshy-install-deb" "$WORK/downloads/example.deb"
grep -qx '<--no-install-recommends>' "$LOG"
grep -q '^</tmp/doshy-deb\..*/package.deb>$' "$LOG"
pass 'package installation passes the staged file as an argument'

mock cp 'exit 1'
: > "$LOG"
if sh "$BIN/doshy-install-deb" "$WORK/downloads/example.deb"; then exit 1; fi
[ ! -s "$LOG" ]
pass 'failed package staging stops before apt runs'

mock xrandr 'if [ "$1" = --query ]; then cat "$HOME/xrandr"; else printf "%s\n" "$*" >> "$LOG"; fi'
mock zenity 'case "$*" in *--list*) echo Mirror ;; esac'
mock pgrep 'exit 1'
cat > "$HOME/xrandr" <<'EOF'
HDMI-1 connected primary 1920x1080+0+0
   1920x1080 60.00*+
   1280x720 60.00
DP-1 connected 2560x1440+1920+0
   2560x1440 60.00*+
   1280x720 60.00
EOF
: > "$LOG"
sh "$BIN/doshy-display"
grep -qx -- '--output HDMI-1 --mode 1280x720 --primary --pos 0x0' "$LOG"
grep -qx -- '--output DP-1 --mode 1280x720 --same-as HDMI-1' "$LOG"
pass 'mirror mode uses a resolution supported by both monitors'

sed -i '/1280x720/d' "$HOME/xrandr"
: > "$LOG"
if sh "$BIN/doshy-display"; then exit 1; fi
[ ! -s "$LOG" ]
pass 'unsupported mirroring stops before changing monitors'

cat > "$HOME/xrandr" <<'EOF'
HDMI-1 connected primary 2560x1440+0+0
   2560x1440 60.00*+
   1920x1080 60.00
DP-1 connected 1920x1080+2560+0
   1920x1080 60.00*+
EOF
mock zenity 'case "$*" in *--list*) echo "Swap screens" ;; esac'
: > "$LOG"
sh "$BIN/doshy-display"
grep -qx -- '--output DP-1 --off' "$LOG"
grep -qx -- '--output HDMI-1 --off' "$LOG"
grep -qx -- '--output DP-1 --mode 1920x1080 --primary --pos 0x0' "$LOG"
grep -qx -- '--output HDMI-1 --mode 2560x1440 --pos 1920x0' "$LOG"
! grep -q -- '--same-as' "$LOG"
pass 'swapping mixed-size monitors clears clone state and keeps native modes'

printf '\nAll regression checks passed.\n'
