#!/bin/sh
# Lists installed GUI programs as jgmenu CSV: name,command,icon
# Used by Start → Applications. Regenerated every time the menu opens, so a
# .deb you just installed shows up immediately.
set -u

# Helpers / settings we already expose elsewhere (keep the list as apps you launch)
hide='doshy-install-deb|doshy-start-menu|install-debian|calamares|thunar-settings|thunar-bulk-rename|thunar-volman-settings|xfce4-terminal-settings|nm-connection-editor|arandr|nitrogen|pavucontrol|zenity|lightdm-gtk-greeter-settings|debian-uxterm|debian-xterm|xdg-desktop-portal'

# Collect .desktop files (user installs override system ones of the same name)
files=""
for dir in "${XDG_DATA_HOME:-$HOME/.local/share}/applications" \
           /usr/local/share/applications /usr/share/applications; do
    [ -d "$dir" ] || continue
    for f in "$dir"/*.desktop; do
        [ -f "$f" ] || continue
        files="$files
$f"
    done
done

printf '%s\n' "$files" | awk -F= -v hide="$hide" -v rows="${1:-}" -v desktop="${XDG_CURRENT_DESKTOP:-Openbox}" '
function quote(s, q) {
    q = sprintf("%c", 39)
    gsub(q, q "\\" q q, s)
    return q s q
}
function csv(s) {
    gsub(/"""/, " ", s)
    return "\"\"\"" s "\"\"\""
}
function matches_desktop(list, entries, current, i, j, n, m) {
    n = split(list, entries, ";")
    m = split(desktop, current, ":")
    for (i = 1; i <= n; i++)
        for (j = 1; j <= m; j++)
            if (entries[i] != "" && entries[i] == current[j]) return 1
    return 0
}
function basename(p) {
    sub(/.*\//, "", p)
    return p
}
BEGIN { n = 0 }
NF == 0 { next }
{
    path = $0
    id = basename(path)
    sub(/\.desktop$/, "", id)
    if (id ~ ("^(" hide ")$")) next
    if (seen[id]++) next

    name = ""; exec = ""; icon = ""; type = "Application"
    nodisplay = 0; hidden = 0; terminal = 0
    only = ""; notshow = ""

    while ((getline line < path) > 0) {
        if (line ~ /^\[/ && line != "[Desktop Entry]") break
        if (line ~ /^Name=/)          { name = substr(line, 6) }
        if (line ~ /^Exec=/)          { exec = substr(line, 6) }
        if (line ~ /^Icon=/)          { icon = substr(line, 6) }
        if (line ~ /^Type=/)          { type = substr(line, 6) }
        if (line ~ /^NoDisplay=true/) { nodisplay = 1 }
        if (line ~ /^Hidden=true/)    { hidden = 1 }
        if (line ~ /^Terminal=true/)  { terminal = 1 }
        if (line ~ /^OnlyShowIn=/)    { only = substr(line, 12) }
        if (line ~ /^NotShowIn=/)     { notshow = substr(line, 11) }
    }
    close(path)

    if (nodisplay || hidden || terminal || type != "Application") next
    if (name == "" || exec == "") next
    if (only != "" && !matches_desktop(only)) next
    if (matches_desktop(notshow)) next

    # Let GIO interpret Exec field codes, quoting and the working directory.
    exec = "gio launch " quote(path)
    if (icon == "") icon = "application-x-executable"

    n++
    if (rows == "--rows") rec[n] = name "\n" exec
    else rec[n] = csv(name) "," csv(exec) "," csv(icon)
    key[n] = tolower(name)
}
END {
    # insertion sort by name
    for (i = 2; i <= n; i++) {
        t = rec[i]; k = key[i]; j = i - 1
        while (j >= 1 && key[j] > k) { rec[j+1] = rec[j]; key[j+1] = key[j]; j-- }
        rec[j+1] = t; key[j+1] = k
    }
    for (i = 1; i <= n; i++) print rec[i]
}
'
