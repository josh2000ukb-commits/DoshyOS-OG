#!/usr/bin/python3
"""Per-user RandR layouts with confirmation and Openbox/Tint2 integration."""
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

TITLE = "Display and monitors"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
STATE = CONFIG / "doshy/displays.json"
NS = "http://openbox.org/3.4/rc"


def run(args, check=True, timeout=20):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, env={**os.environ, "LC_ALL": "C"})
    if check and result.returncode:
        raise ValueError(result.stderr.strip() or "Could not run " + args[0])
    return result


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".display-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def parse_outputs(text):
    outputs, current, edid = [], None, False
    for line in text.splitlines():
        header = re.match(r"^(\S+) (connected|disconnected)\b(.*)", line)
        if header:
            current, edid = None, False
            if header[2] == "disconnected":
                continue
            geometry = re.search(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", header[3])
            tail = header[3].split("(")[0].split()
            current = dict(name=header[1], primary="primary" in tail, mode=None, rate=None,
                           x=int(geometry[3]) if geometry else 0,
                           y=int(geometry[4]) if geometry else 0,
                           rotation=next((v for v in tail if v in ("left", "right", "inverted")), "normal"),
                           modes={}, preferred=None, edid="")
            outputs.append(current)
        elif current is not None:
            if line.strip() == "EDID:":
                edid = True
                continue
            if edid and re.fullmatch(r"[0-9a-fA-F]{32}", line.strip()):
                current["edid"] += line.strip().lower()
                continue
            edid = False
            mode = re.match(r"^\s+(\d+x\d+)\s+([0-9].*)", line)
            if mode:
                rates = re.findall(r"(\d+(?:\.\d+)?)([*+]*)", mode[2])
                current["modes"][mode[1]] = [r for r, flags in rates]
                for rate, flags in rates:
                    if "*" in flags:
                        current["mode"], current["rate"] = mode[1], rate
                    if "+" in flags:
                        current["preferred"] = mode[1]
    return outputs


def query():
    outputs = parse_outputs(run(["xrandr", "--query", "--prop"]).stdout)
    if not outputs:
        raise ValueError("No connected screens were detected.")
    return outputs


def profile_key(outputs):
    identity = sorted((o["name"], o["edid"]) for o in outputs)
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def snapshot(outputs):
    keys = ("name", "primary", "mode", "rate", "x", "y", "rotation")
    return [{k: o[k] for k in keys} for o in outputs]


def normalize(layout):
    active = [o for o in layout if o["mode"]]
    if not active:
        raise ValueError("At least one screen must remain enabled.")
    primary = next((o for o in active if o["primary"]), active[0])
    min_x, min_y = min(o["x"] for o in active), min(o["y"] for o in active)
    for o in layout:
        o["primary"] = o is primary
        o["x"] -= min_x
        o["y"] -= min_y
    return layout


def framebuffer(layout):
    """Return the RandR framebuffer needed to contain every active output."""
    right = bottom = 0
    for output in layout:
        if not output["mode"]:
            continue
        width, height = (int(value) for value in output["mode"].split("x"))
        if output["rotation"] in ("left", "right"):
            width, height = height, width
        right = max(right, output["x"] + width)
        bottom = max(bottom, output["y"] + height)
    return right, bottom


def command(layout, outputs):
    available = {o["name"]: o for o in outputs}
    if len(layout) != len(available) or {o["name"] for o in layout} != set(available):
        raise ValueError("Connected screens have changed. Open Display and monitors again.")
    layout = normalize(copy.deepcopy(layout))
    width, height = framebuffer(layout)
    args = ["xrandr", "--fb", f"{width}x{height}"]
    for o in layout:
        args += ["--output", o["name"]]
        if o["mode"] is None:
            args += ["--off"]
            continue
        modes = available[o["name"]]["modes"]
        if o["mode"] not in modes or (o["rate"] and o["rate"] not in modes[o["mode"]]):
            raise ValueError("A saved resolution or refresh rate is no longer supported.")
        if o["rotation"] not in ("normal", "left", "right", "inverted"):
            raise ValueError("Invalid screen rotation.")
        if any(type(o[k]) is not int or not 0 <= o[k] <= 32767 for k in ("x", "y")):
            raise ValueError("Screen positions must be between 0 and 32767.")
        args += ["--mode", o["mode"], "--pos", f'{o["x"]}x{o["y"]}',
                 "--rotate", o["rotation"]]
        if o["rate"]:
            args += ["--rate", o["rate"]]
        if o["primary"]:
            args += ["--primary"]
    return args


def apply(layout):
    args = command(layout, query())
    run(args[:1] + ["--dryrun"] + args[1:])
    # Clear old CRTCs first.  This makes a swap deterministic when the larger
    # monitor changes sides and prevents stale framebuffer/mouse coordinates.
    names = [output["name"] for output in query()]
    off = ["xrandr"]
    for name in names:
        off += ["--output", name, "--off"]
    run(off)
    run(args)


def save(outputs):
    try:
        data = json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        data = {"format": 1, "profiles": {}}
    data["profiles"][profile_key(outputs)] = snapshot(outputs)
    atomic(STATE, json.dumps(data, indent=2) + "\n")


def main_index(outputs, xinerama):
    primary = next((o for o in outputs if o["primary"] and o["mode"]), None)
    if primary:
        for match in re.finditer(r"head #(\d+):\s+\d+x\d+ @ (-?\d+),(-?\d+)", xinerama):
            if (int(match[2]), int(match[3])) == (primary["x"], primary["y"]):
                return str(int(match[1]) + 1)
    # Xorg places the RandR primary output first in Xinerama by default.
    return "1"


def openbox_config(text, index):
    ET.register_namespace("", NS)
    root = ET.fromstring(text, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    def child(parent, tag):
        node = parent.find("{" + NS + "}" + tag)
        return ET.SubElement(parent, "{" + NS + "}" + tag) if node is None else node
    placement = child(root, "placement")
    child(placement, "monitor").text = "Primary"
    child(placement, "primaryMonitor").text = index
    applications = child(root, "applications")
    for rule in list(applications):
        if rule.get("doshy-main-screen") == "yes":
            applications.remove(rule)
    rule = ET.SubElement(applications, "{" + NS + "}application",
                         {"name": "*", "type": "normal", "doshy-main-screen": "yes"})
    position = ET.SubElement(rule, "{" + NS + "}position", {"force": "yes"})
    for tag, value in (("x", "center"), ("y", "center"), ("monitor", index)):
        child(position, tag).text = value
    return ET.tostring(root, encoding="unicode") + "\n"


def integrate(restart_panel=False):
    outputs = query()
    index = main_index(outputs, run(["xdpyinfo", "-ext", "XINERAMA"], check=False).stdout)
    for relative, default in (("openbox/rc.xml", "/etc/xdg/openbox/rc.xml"),
                              ("tint2/tint2rc", "/etc/xdg/tint2/tint2rc")):
        path = CONFIG / relative
        original = path.read_text() if path.exists() else Path(default).read_text()
        if relative.startswith("openbox"):
            updated = openbox_config(original, index)
        else:
            updated = re.sub(r"(?m)^\s*panel_monitor\s*=.*$", "panel_monitor = primary", original)
            if not re.search(r"(?m)^panel_monitor = primary$", updated):
                updated += "\npanel_monitor = primary\n"
        if original != updated or not path.exists():
            backup = path.with_name(path.name + ".before-doshy-1.0.2")
            if path.exists() and not backup.exists():
                atomic(backup, original)
            atomic(path, updated)
    run(["openbox", "--reconfigure"], check=False)
    if restart_panel:
        run(["pkill", "-u", str(os.getuid()), "-x", "tint2"], check=False)
        time.sleep(0.2)
        subprocess.Popen(["tint2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run(["doshy-wallpaper", "--restore"], check=False)


def restore():
    outputs = query()
    try:
        data = json.loads(STATE.read_text())
        layout = data["profiles"].get(profile_key(outputs))
        if layout:
            apply(layout)
    except (OSError, ValueError, KeyError, TypeError):
        # Never overwrite a profile when a cable/mode is unavailable.
        pass
    outputs = query()
    active = [o for o in outputs if o["mode"]]
    if not active:
        run(["xrandr", "--output", outputs[0]["name"], "--auto", "--primary"])
    elif not any(o["primary"] for o in active):
        run(["xrandr", "--output", active[0]["name"], "--primary"])


def dialog(kind, text, *args):
    return run(["zenity", "--" + kind, "--no-markup", "--title=" + TITLE,
                "--width=580", "--text=" + text, *args], check=False, timeout=None)


def choose(text, values):
    result = dialog("list", text, "--column=Choice", "--height=450", *values)
    return result.stdout.strip() if result.returncode == 0 else ""


def transaction(layout, before):
    kept = False
    try:
        apply(layout)
        integrate(True)
        result = dialog("question", "Keep these display settings?\n\nThe previous layout returns in 20 seconds unless you choose Keep.",
                        "--timeout=20", "--ok-label=Keep", "--cancel-label=Restore previous")
        if result.returncode == 0:
            save(query())
            kept = True
    finally:
        if not kept:
            try:
                apply(before)
            except ValueError:
                restore()
            integrate(True)


def gui():
    outputs = query()
    before = snapshot(outputs)
    layout = copy.deepcopy(before)
    status = "\n".join(o["name"] + (" (main)" if o["primary"] else "") + ": " +
                       (f'{o["mode"]} at {o["x"]},{o["y"]}' if o["mode"] else "off") for o in outputs)
    action = choose(status + "\n\nChanges are saved for this user and these connected monitors.",
                    ["Choose main screen", "Place screens side by side", "Swap screens",
                     "Mirror screens", "Use one screen only", "Resolution and refresh rate",
                     "Position and rotation", "Advanced: drag screens", "Save current layout"])
    if not action:
        return
    if action == "Advanced: drag screens":
        # Watch the first ARandR Apply, then close its editor and run our own
        # confirmation/rollback. Never execute ARandR's saved shell scripts.
        editor = subprocess.Popen(["arandr"])
        try:
            while editor.poll() is None:
                time.sleep(0.5)
                changed = snapshot(query())
                if changed != before:
                    editor.terminate()
                    editor.wait(timeout=5)
                    transaction(changed, before)
                    return
            changed = snapshot(query())
            if changed != before:
                transaction(changed, before)
        finally:
            if editor.poll() is None:
                editor.terminate()
        return
    if action == "Save current layout":
        transaction(layout, before)
        return
    if action in ("Choose main screen", "Use one screen only", "Resolution and refresh rate", "Position and rotation"):
        names = [o["name"] for o in outputs if o["mode"] or action == "Use one screen only"]
        name = choose("Choose a screen", names)
        if not name:
            return
        target = next(o for o in layout if o["name"] == name)
        hardware = next(o for o in outputs if o["name"] == name)
        if action == "Choose main screen":
            for o in layout:
                o["primary"] = o is target
        elif action == "Use one screen only":
            for o in layout:
                o.update(mode=(hardware["preferred"] or next(iter(hardware["modes"]))) if o is target else None,
                         rate=None, primary=o is target, x=0, y=0, rotation="normal")
        elif action == "Resolution and refresh rate":
            mode = choose("Choose a resolution", list(hardware["modes"]))
            if not mode:
                return
            rate = choose("Choose a refresh rate (Hz)", hardware["modes"][mode])
            if not rate:
                return
            target.update(mode=mode, rate=rate)
        else:
            result = dialog("forms", "Position in pixels. Use 0,0 for the upper-left screen.\nRotation: normal, left, right or inverted.",
                            "--add-entry=Horizontal position (X)", "--add-entry=Vertical position (Y)",
                            "--add-entry=Rotation", "--separator=|")
            if result.returncode:
                return
            x, y, rotation = result.stdout.strip().split("|")
            target.update(x=int(x), y=int(y), rotation=rotation or "normal")
    else:
        ordered = sorted(layout, key=lambda o: (o["x"], o["y"]))
        if action == "Swap screens":
            ordered.reverse()
        common = set(outputs[0]["modes"])
        for o in outputs:
            common &= set(o["modes"])
        if action == "Mirror screens" and not common:
            raise ValueError("These screens have no common resolution for mirroring.")
        mirror = next((m for m in outputs[0]["modes"] if m in common), None)
        x = 0
        for o in ordered:
            hardware = next(h for h in outputs if h["name"] == o["name"])
            mode = mirror if action == "Mirror screens" else hardware["preferred"] or next(iter(hardware["modes"]))
            o.update(mode=mode, rate=None, x=x, y=0, rotation="normal")
            if action != "Mirror screens":
                x += int(mode.split("x")[0])
    transaction(normalize(layout), before)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--fixup", action="store_true")
    args = parser.parse_args()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with (STATE.parent / "display.lock").open("w") as lock:
        if args.watch:
            with (STATE.parent / "display-watch.lock").open("w") as singleton:
                try:
                    fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return
                last = None
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        key = profile_key(query())
                        if key != last:
                            restore()
                            integrate(True)
                            last = key
                    except (BlockingIOError, OSError, ValueError, subprocess.SubprocessError):
                        pass
                    finally:
                        fcntl.flock(lock, fcntl.LOCK_UN)
                    time.sleep(3)
        else:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.restore:
                restore()
                integrate()
            elif args.fixup:
                integrate(True)
            else:
                gui()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError, subprocess.SubprocessError) as error:
        if "--restore" not in sys.argv and "--watch" not in sys.argv:
            dialog("error", str(error))
        print(str(error), file=sys.stderr)
        sys.exit(1)
