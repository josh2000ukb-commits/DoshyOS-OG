#!/usr/bin/python3
"""Debian graphics driver manager. All privileged plans are recomputed as root."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

STORAGE = Path("/var/lib/doshy-drivers")
HELPER = "/usr/local/bin/doshy-drivers-helper"
NVIDIA = {"nvidia-driver", "nvidia-tesla-535-driver", "nvidia-tesla-470-driver"}
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"}
APT = ["-o", "Dir::Etc::sourcelist=/usr/share/doshy/graphics.sources",
       "-o", "Dir::Etc::sourceparts=-", "-o", "Dir::State::lists=/var/lib/doshy-drivers/lists",
       "-o", "APT::Get::List-Cleanup=0", "-o", "Acquire::Retries=2",
       "-o", "Acquire::http::Timeout=30", "-o", "Acquire::https::Timeout=30"]


def run(args, check=True, timeout=120):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=ENV, timeout=timeout)
    if check and result.returncode:
        raise ValueError((result.stderr or result.stdout).strip()[-6000:] or "Command failed: " + args[0])
    return result


def write_json(path, data):
    fd, name = tempfile.mkstemp(prefix=".drivers-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def hardware(root=Path("/sys/bus/pci/devices")):
    devices = []
    for device in sorted(root.iterdir()):
        if not (device / "class").read_text().strip().startswith("0x03"):
            continue
        vendor = (device / "vendor").read_text().strip()
        ident = (device / "device").read_text().strip()
        driver = (device / "driver").resolve().name if (device / "driver").exists() else "none"
        name = run(["lspci", "-s", device.name], check=False).stdout.strip()
        devices.append({"slot": device.name, "vendor": vendor, "device": ident,
                        "driver": driver, "name": name or f"{vendor}:{ident}"})
    return devices


def live():
    return Path("/run/live/medium").exists()


def secure_boot():
    variables = list(Path("/sys/firmware/efi/efivars").glob("SecureBoot-*"))
    if not Path("/sys/firmware/efi").exists():
        return False
    if not variables:
        return None
    try:
        return variables[0].read_bytes()[4] == 1
    except (OSError, IndexError):
        return None


def recommended_nvidia(text):
    # Match the final recommendation, not mentions of alternative legacy drivers.
    matches = re.findall(r"^\s*(nvidia(?:-tesla-\d+)?-driver)\s*$", text, re.M)
    supported = sorted(set(matches) & NVIDIA)
    return supported[0] if len(supported) == 1 else None


def packages_for(devices, choice, is_live, secure, detection=""):
    if not devices:
        raise ValueError("No PCI graphics hardware was detected. No driver changes are needed through this tool.")
    vendors = {d["vendor"] for d in devices}
    packages = {"libgl1-mesa-dri", "libglx-mesa0", "mesa-vulkan-drivers"}
    if "0x8086" in vendors:
        packages.add("firmware-misc-nonfree")
    if "0x1002" in vendors:
        packages.update(("firmware-amd-graphics", "xserver-xorg-video-amdgpu", "xserver-xorg-video-radeon"))
    if "0x10de" in vendors:
        if choice == "nvidia":
            if is_live:
                raise ValueError("Proprietary NVIDIA installation needs a full installation. A live USB boots a fixed kernel/initramfs which this tool cannot replace. Open drivers and firmware can still be updated on a persistent USB.")
            if secure is not False:
                raise ValueError("Secure Boot is enabled or its state cannot be verified. Keep the open driver, or arrange DKMS signing and MOK enrollment before using a proprietary NVIDIA driver. See Graphics drivers → Recovery help.")
            recommended = recommended_nvidia(detection)
            if not recommended:
                raise ValueError("Debian 12 did not identify one supported NVIDIA driver for this hardware. Keep the current driver; a newer OS base may be required.")
            packages.add(recommended)
            packages.add("linux-headers-amd64")
        elif choice == "open":
            packages.update(("xserver-xorg-video-nouveau", "firmware-misc-nonfree"))
    elif choice == "nvidia":
        raise ValueError("No NVIDIA graphics card was detected.")
    if not is_live:
        packages.add("linux-image-amd64")
    return sorted(packages)


def versions(packages):
    output = run(["apt-cache", *APT, "policy", *packages]).stdout
    result, name = {}, None
    for line in output.splitlines():
        if line and not line[0].isspace() and line.endswith(":"):
            name = line[:-1].removesuffix(":amd64")
            result[name] = {}
        elif name:
            match = re.match(r"\s*(Installed|Candidate): (.+)", line)
            if match:
                result[name][match[1].lower()] = match[2]
    for package in packages:
        if result.get(package, {}).get("candidate", "(none)") == "(none)":
            raise ValueError(f"No Debian 12 candidate is available for {package}. Check the connection and choose Check for updates again.")
    return result


def simulation(specs, is_live):
    result = run(["apt-get", *APT, "--simulate", "--no-remove", "--no-install-recommends", "install", *specs])
    operations = [line for line in result.stdout.splitlines() if line.startswith(("Inst ", "Remv ", "Conf "))]
    if any(line.startswith("Remv ") for line in operations):
        raise ValueError("The proposed driver update would remove packages. No changes were made.")
    if is_live and any(re.match(r"Inst (linux-(image|headers)|.*dkms|nvidia-(driver|kernel|tesla)|initramfs-tools|grub|shim)", line) for line in operations):
        raise ValueError("This transaction would change boot or kernel-driver packages on a live USB. A full installation is required for that change.")
    return operations


def installed_state():
    return run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\n"]).stdout


def prepare(choice):
    # Reuse the updater's platform and persistent-root checks and shared lock.
    devices = hardware()
    detection = ""
    if choice == "nvidia":
        recommendations = []
        for device in devices:
            if device["vendor"] == "0x10de":
                text = run(["nvidia-detect", "10de:" + device["device"].removeprefix("0x")], check=False).stdout
                recommendations.append(recommended_nvidia(text))
        if recommendations and None not in recommendations and len(set(recommendations)) == 1:
            detection = recommendations[0]
    packages = packages_for(devices, choice, live(), secure_boot(), detection)
    (STORAGE / "lists/partial").mkdir(parents=True, exist_ok=True)
    run(["apt-get", *APT, "-o", "APT::Update::Error-Mode=any", "update"], timeout=300)
    available = versions(packages)
    specs = [f'{p}={available[p]["candidate"]}' for p in packages]
    operations = simulation(specs, live())
    plan = {"token": uuid.uuid4().hex, "created": time.time(), "choice": choice,
            "devices": devices, "specs": specs, "versions": available, "operations": operations,
            "installed": installed_state(), "live": live(), "secure_boot": secure_boot()}
    write_json(STORAGE / "plan.json", plan)
    return {k: v for k, v in plan.items() if k != "installed"}


def install(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Invalid approval token. Check for updates again.")
    plan = json.loads((STORAGE / "plan.json").read_text())
    if token != plan["token"] or not 0 <= time.time() - plan["created"] <= 900:
        raise ValueError("The preview expired. Check for updates again.")
    if (hardware() != plan["devices"] or installed_state() != plan["installed"] or
            live() != plan["live"] or secure_boot() != plan["secure_boot"]):
        raise ValueError("The system changed after the preview. Check for updates again.")
    if simulation(plan["specs"], live()) != plan["operations"]:
        raise ValueError("The package transaction changed. Check for updates again.")
    # Save recovery information before APT starts; never pretend this is a snapshot.
    plan["status"] = "in-progress"
    write_json(STORAGE / "last-transaction.json", plan)
    (STORAGE / "plan.json").unlink()
    result = run(["apt-get", *APT, "--yes", "--no-remove", "--no-install-recommends",
                  "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold",
                  "install", *plan["specs"]], check=False, timeout=None)
    plan.update(status="complete" if result.returncode == 0 else "failed",
                output=result.stdout + result.stderr)
    write_json(STORAGE / "last-transaction.json", plan)
    if result.returncode:
        raise ValueError("The driver update did not finish. See Recovery help and /var/lib/doshy-drivers/last-transaction.json.\n\n" + (result.stderr or result.stdout)[-3500:])
    return {"status": "complete", "reboot": any(line.startswith("Inst ") for line in plan["operations"])}


def helper(action, value):
    if os.geteuid() != 0:
        raise ValueError("Administrator authentication is required.")
    # No imports from the working directory or the caller's PYTHONPATH.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from update_core import Updater
    updater = Updater()
    with updater.lock():
        updater.check_system()
        STORAGE.mkdir(parents=True, exist_ok=True)
        if action == "prepare" and value in ("open", "nvidia"):
            return prepare(value)
        if action == "install":
            return install(value)
        raise ValueError("Unknown driver-manager action.")


def dialog(kind, text, *args):
    return subprocess.run(["zenity", "--" + kind, "--no-markup", "--title=Graphics drivers",
                           "--width=680", "--text=" + text, *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def privileged(action, value):
    # Pipe-based progress without GTK or a root graphical process. Closing the
    # progress window does not terminate APT midway through a transaction.
    progress = subprocess.Popen(["zenity", "--progress", "--pulsate", "--no-cancel", "--auto-close",
                                 "--title=Graphics drivers", "--text=Checking Debian packages…" if action == "prepare" else "--text=Installing graphics updates. Please keep the computer on…"],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        result = subprocess.run(["pkexec", HELPER, action, value], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    finally:
        if progress.stdin:
            progress.stdin.close()
        if progress.poll() is None:
            progress.terminate()
        progress.wait()
    if result.returncode in (126, 127):
        return None
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Administrator operation failed.")
    return json.loads(result.stdout)


RECOVERY = """Driver recovery

Your files are not removed by a driver update. Package removal is disabled.
Driver changes may require a restart; this program never restarts automatically.

If the desktop fails after a full-install update, use GRUB → Advanced options to boot the previous kernel. You can also press Ctrl+Alt+F3 for a text login. Check /var/lib/doshy-drivers/last-transaction.json for the exact package list, previous versions and installation log. APT history is in /var/log/apt/history.log.

For an interrupted package installation, an administrator can run sudo dpkg --configure -a and then sudo apt-get -f install from the text terminal. Review any proposed changes.

There is no automatic driver downgrade or disk snapshot. Previous package versions may no longer be available. System updates → Restore previous update restores DoshyOS files, not Debian graphics packages.

Secure Boot: proprietary NVIDIA modules require a trusted signing key. Debian's instructions explain DKMS keys and MOK enrollment: https://wiki.debian.org/SecureBoot . This tool permits proprietary installation only when Secure Boot is off; it does not change firmware settings.

Live USB: only userspace graphics and firmware updates are supported, with full persistence. The USB's boot kernel/initramfs stays unchanged. A full install is required for proprietary NVIDIA drivers and kernel updates."""


def gui():
    while True:
        devices = hardware()
        summary = "\n".join(d["name"] + "\n  Active driver: " + d["driver"] for d in devices) or "No PCI graphics device detected."
        summary += "\n\nUpdates use signed Debian 12 repositories. Intel and AMD use built-in kernel drivers plus Mesa and firmware."
        summary += "\nLive USB: full persistence is required; boot drivers stay unchanged." if live() else "\nFull installation: updates can include the Debian kernel."
        actions = ["Check for open-driver and firmware updates"]
        if any(d["vendor"] == "0x10de" for d in devices):
            actions.append("Check for recommended NVIDIA driver")
            summary += "\nUpdating open-driver packages does not switch an installed proprietary NVIDIA driver."
        actions.append("Recovery help")
        result = dialog("list", summary, "--column=Action", "--height=460", *actions)
        if result.returncode:
            return
        action = result.stdout.strip()
        if action == "Recovery help":
            dialog("info", RECOVERY)
            continue
        choice = "nvidia" if action == "Check for recommended NVIDIA driver" else "open"
        try:
            plan = privileged("prepare", choice)
            if plan is None:
                continue
            if not any(line.startswith("Inst ") for line in plan["operations"]):
                dialog("info", "Your selected graphics packages are up to date. No installation is needed.")
                continue
            text = "Install these graphics updates?\n\n" + "\n".join(
                f'{p}: {v.get("installed", "(none)")} → {v["candidate"]}' for p, v in plan["versions"].items())
            text += "\n\nAdditional Debian dependencies:\n" + "\n".join(plan["operations"])
            text += "\n\nA restart may be required. Keep the computer powered on. Recovery help explains how to recover if a driver fails."
            # Scrollable preview keeps large kernel/DKMS transactions reviewable.
            with tempfile.TemporaryFile(mode="w+") as preview:
                preview.write(text)
                preview.seek(0)
                approved = subprocess.run(["zenity", "--text-info", "--title=Review graphics update",
                                           "--width=780", "--height=560", "--checkbox=I have reviewed these changes",
                                           "--ok-label=Install"], stdin=preview).returncode == 0
            if approved:
                result = privileged("install", plan["token"])
                if result:
                    dialog("info", "Graphics updates installed successfully.\n\nRestart when convenient to finish using the updated drivers." if result["reboot"] else "Graphics packages are already up to date.")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            dialog("error", str(error))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", action="store_true")
    parser.add_argument("action", nargs="?")
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    try:
        if args.helper:
            print(json.dumps(helper(args.action, args.value)))
        else:
            gui()
    except Exception as error:
        if not args.helper:
            dialog("error", str(error))
        print(str(error), file=sys.stderr)
        sys.exit(1)
