# doshyos-OG

A small Linux operating system (based on Debian 12) with a Windows-style desktop:

- A taskbar along the bottom with a **Start** button, pinned apps, running windows, tray (Wi-Fi, volume, battery) and clock.
- A clean desktop with a wallpaper; right-click it for a small menu.
- Applications: **Files** (file explorer), **Terminal**, **Firefox** and **Discord**, plus a **Start → Applications**
  list of everything installed.
- **Install downloaded programs**: double-click a `.deb` file (or right-click → *Install package*) and it is installed
  and added to Start → Applications.
- **Login screen with multiple users**: pick your user at the login screen; add users, set passwords and admins in
  Settings → Users.
- **Settings** in the Start menu: Keyboard language, Display and monitors (saved layouts and main screen), Graphics drivers, Wallpaper, Sound,
  Network, Users, System updates, Install a package.
- **Updates without wiping the drive**: System updates installs numbered releases from your public GitHub
  repository, keeping personal files and local settings. See [the setup and publishing guide](UPDATING.md).
- Designed to **run from an external drive** (USB stick / external SSD): boots straight into the desktop, and with a
  persistence partition (see below) everything you change or download is kept on the drive between reboots.
  The computer's internal disk is never touched.

Windows-style shortcuts: `Alt+Tab`, `Alt+F4`, `Win+D` (show desktop), `Win+Up/Down/Left/Right` (maximise / restore / snap),
`Win+E` (Files), `Win+T` (Terminal), `Win+F` (Firefox), `Win+I` (Settings), `Win+A` (Applications), `Win+S` (Start menu).

## 1. Build the ISO

The image is built with Debian `live-build`, which needs a Linux environment. From Windows:

```powershell
.\build.ps1
```

`build.ps1` uses Docker Desktop if it is running; otherwise it uses WSL, and installs a Debian WSL distribution
automatically if you don't have one. Give it 20-60 minutes and ~10 GB of free space the first time; it downloads
about 1.6 GB of packages (rebuilds take ~10 minutes). The result is written to `out\doshyos-amd64.hybrid.iso`.

Options:

```powershell
.\build.ps1 -Locale en_GB.UTF-8 -Keyboard gb -Timezone Europe/London   # UK keyboard/locale as the default
.\build.ps1 -Backend wsl                                              # force WSL even if Docker exists
```

On a Linux machine (Debian/Ubuntu) you can simply run `sudo ./build.sh`.

### If WSL 2 isn't set up on your PC

`build.ps1` will tell you. Fixing it is a one-off: open PowerShell **as Administrator**, run
`wsl --install --no-distribution`, reboot, then run `.\build.ps1` again. If it still complains, turn on
*Virtualization* / *SVM Mode* / *VT-x* in your BIOS.

### Or let GitHub build it for you (nothing to install)

1. Create a new repository on GitHub and push this folder to it (`git init`, `git add .`, `git commit`, `git push`).
2. GitHub Actions runs `.github/workflows/build-iso.yml` automatically (about 30-40 minutes).
3. Open the run under the **Actions** tab and download the `doshy-os-iso` artifact; unzip it to get the `.iso`.
   Update tags such as `v1.0.2` publish a small in-place update instead; see [UPDATING.md](UPDATING.md).

## 2. Put it on your external drive

You need [Rufus](https://rufus.ie) (free, no install needed). The drive will be **completely erased**.

1. Plug in the external drive and open Rufus.
2. **Device**: pick your external drive (check the size - not your Windows disk).
3. **Boot selection**: click *SELECT* and choose `out\doshyos-amd64.hybrid.iso`.
4. **Persistent partition size**: drag the slider to the right - this is the space where your files, downloads,
   Firefox/Discord logins and any changes are saved. Use most of the drive.
5. **Partition scheme**: `MBR`, **Target system**: `BIOS or UEFI` (works on the most computers).
6. Press **START**. If Rufus asks about GRUB versions, choose **Yes** (download the matching one). If it asks how to
   write the image, choose **"Write in ISO Image mode"** - that is the mode that supports the persistent partition.
7. When it finishes, reboot the computer, open the boot menu (usually `F12`, `F2`, `Esc` or `Del`) and pick the
   external drive. If it doesn't show up, disable *Secure Boot* in the BIOS/UEFI settings.

The boot menu starts doshy OS automatically after 5 seconds and shows the login screen. The built-in account is
user **`doshy`** with password **`live`** (change it in Settings → Users → Change password). Anything you save
or change is kept on the drive; unplug it and the computer is exactly as it was.

If you would rather skip the login screen, choose *Advanced options → Start doshy OS with automatic login* in the
boot menu.

> Rufus labels the extra partition `persistence` and writes `persistence.conf` into it, which is what doshy OS
> looks for at boot. If you use another tool (e.g. balenaEtcher) you get a working but *non-persistent* system;
> to add persistence by hand, create an extra ext4 partition labelled `persistence` containing a file
> `persistence.conf` with the single line `/ union`.

## 3. (Optional) Full install onto an external SSD

For a big external SSD you may prefer a real installation (faster, normal updates, no persistence layer).
Boot the doshy OS stick, plug in the target drive, click **Start → Install doshy OS to a drive**, and on the
partitioning page make sure you pick the **external drive** (not the internal disk). Tick *"Log in automatically"*
on the Users page to keep the boot-straight-to-desktop behaviour.

## Using it

- **Login**: pick a user on the login screen. Built-in account is **`doshy`** / password **`live`**.
  **Settings → Users**: administrators can add people and **edit** username, full name, password and
  whether they are an Administrator. Standard users can only change their own password.
  **Start → Switch user** goes back to the login screen.
- **Start menu**: Files, Terminal, Firefox, Discord, **Applications** (every installed program), Settings ▸,
  Install, Switch user, Log out / Restart / Shut down.
- **Installing a .deb**: download it (usually in Files → Downloads), then double-click it, or right-click →
  *Install package*, or **Settings → Install a package**. After it finishes, open it from
  **Start → Applications** or press `Win+A`.
- **Settings → Keyboard language**: pick your keyboard's language (e.g. *English (UK)*). It applies immediately
  and is remembered.
- **Settings → Display and monitors**: choose the main screen for the bottom bar and new applications;
  arrange monitors and choose resolution, refresh rate and rotation. Confirm within 20 seconds to save
  the layout for future logins/reboots. *Advanced* supports dragging screens; its first Apply returns to
  DoshyOS for confirmation. Each user and monitor combination has its own saved profile.
- **Settings → Graphics drivers**: detect hardware and check/install signed Debian graphics packages.
  Review the proposed changes before installing. Full persistence is required on live USBs; proprietary
  NVIDIA and boot-kernel updates require a full installation. See [1.0.2 release notes](updates/v1.0.2.md)
  for supported driver paths, Secure Boot limits and recovery information.
- **Settings → Wallpaper**: pick an image; it is shown in full on **every** monitor (not stretched across both).
- The built-in `doshy` account can use `sudo` without a password on the live USB. New Administrator users
  use their own password.

## Customising

| What                         | Where                                                                 |
|------------------------------|-----------------------------------------------------------------------|
| Packages                     | `config/package-lists/*.list.chroot`                                  |
| Discord download/install     | `config/hooks/normal/0200-discord.hook.chroot`                        |
| Taskbar look/layout          | `config/includes.chroot/etc/xdg/tint2/tint2rc`                        |
| Start menu entries           | `config/includes.chroot/usr/share/doshy/menu.sh`                      |
| Applications list            | `config/includes.chroot/usr/share/doshy/apps.sh`, `usr/local/bin/doshy-apps` |
| .deb installer               | `config/includes.chroot/usr/local/bin/doshy-install-deb`               |
| Users / login                | `usr/local/bin/doshy-users`, `usr/local/lib/doshy/login-setup`         |
| Start menu style             | `config/includes.chroot/usr/share/doshy/jgmenurc`                     |
| Settings tools               | `config/includes.chroot/usr/local/bin/doshy-settings`, `doshy-keyboard` |
| Keyboard shortcuts / windows | `config/includes.chroot/etc/xdg/openbox/rc.xml`                       |
| Right-click desktop menu     | `config/includes.chroot/etc/xdg/openbox/menu.xml`                     |
| Autostarted programs         | `config/includes.chroot/etc/xdg/openbox/autostart`                    |
| Wallpaper / logo             | `config/includes.chroot/usr/share/backgrounds/doshy.png`, `.../doshy/logo.png` |
| Boot options, OS name        | `auto/config`                                                         |
| Boot menu (entries, timeout, splash) | `config/bootloaders/grub-pc/` (UEFI), `config/bootloaders/syslinux_common/` + `isolinux/` (BIOS) |
| Build-time tweaks            | `config/hooks/normal/0100-doshy.hook.chroot`                          |
