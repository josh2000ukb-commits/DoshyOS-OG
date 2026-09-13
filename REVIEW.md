# doshy OS source review — 13 September 2026

This is a Debian 12 live-build distribution with an Openbox desktop, not a custom kernel. The existing design remains intact. No ISO was built, booted, or replaced during this review.

## How it fits together

- `build.ps1` selects Docker or WSL. `build.sh` prepares live-build and collects the ISO. `auto/` configures the Debian image and wraps its build/clean commands.
- `config/package-lists/` supplies the kernel's userspace environment, drivers, desktop applications and Calamares installer. Build hooks brand Debian and install Discord.
- BIOS and UEFI menu templates select persistence, login and graphics options. LightDM starts Openbox; `login-setup` applies the live-session login policy.
- Openbox supplies window management and shortcuts. Tint2 supplies the taskbar, jgmenu the Start menu, and shell scripts with Zenity supply settings and account/package dialogs.
- Settings live in the user's XDG configuration directory. USB persistence is provided by Debian live-boot, separately from the desktop scripts.

## Fixes made

| Area | Fault and correction |
| --- | --- |
| Users window | Space-containing extra-button labels were word-split. Arguments are now individually quoted. |
| Standard-user passwords | Changing one's own password required administrator authentication. Standard users now run the normal `passwd` flow in a terminal. |
| Account editing | Optional groups missing from the system could make the helper exit under `set -e`. Missing groups are now skipped successfully. |
| Password records | Colon-containing passwords were truncated during edits. The complete password is preserved; multiple records and mismatched account names are rejected before mutation. |
| Account type | An empty edit-form selection could demote an administrator. An unchanged selection preserves the existing type. |
| Account safeguards | Last-administrator demotion is checked before editing other fields. Active-account renames are rejected and the UI no longer promises they will succeed. Reserved high UIDs are excluded from edit/removal. |
| Live login | Locked accounts were reset to the known live password. Only blank passwords are initialized, including when automatic login is selected. |
| Display positioning | AWK could print both the preferred mode and a fallback, breaking arithmetic. It now returns one mode, explicitly applies it and uses its width for placement. Simple layouts reset rotation as well. |
| Display failures | Invalid framebuffer reset requests were removed. Failed display operations now stop instead of showing success. |
| Mirroring | Different native resolutions could crop the shared desktop. Mirror mode chooses a common resolution and refuses unsupported arrangements before changing screens. |
| Multi-user processes | Start-menu toggles and taskbar restarts now target processes belonging to the current user. |
| Wallpaper | Connected but disabled outputs were included, rendering failures were hidden and failed choices were saved. Only active outputs are used, failures are returned and saving happens after successful rendering. |
| Keyboard | Switching to a base layout could leave the old variant active. The variant is explicitly cleared. |
| Applications | System entries took precedence over user overrides. User entries now win, including hidden overrides, and visibility filters match the current desktop. |
| Application launching | Hand-editing `Exec` corrupted commas and field codes. GIO now launches the original desktop file; its package is explicitly included. Menu CSV is quoted, and the searchable chooser selects the command directly so duplicate names are distinguishable. |
| Package installation | Failed temporary-file creation/copy could be ignored. Staging failures now stop installation; the package path is passed as an argument to the privileged shell and temporary files are cleaned on exit. |
| Build errors | `tee` hid live-build's exit status. The wrapper uses Bash `pipefail` so failed builds remain failures. |
| Windows build paths | WSL path conversion supplied literal quote characters and interpolated paths into shell code. It now checks conversion and passes the path as a positional argument. |
| Tagged releases | The GitHub release step lacked an explicit write permission. The build job now requests `contents: write`. |

The application-launching changes follow [GIO's desktop-file launch support](https://mail.gnome.org/archives/commits-list/2020-December/msg02950.html) and [jgmenu's triple-quoted CSV format](https://manpages.debian.org/testing/jgmenu/jgmenu.1.en.html).

## Verification

`tests/regressions.sh` passed all 18 checks in Debian WSL. It copies scripts into a temporary directory and mocks account, display, package-manager and build commands; it does not build an image or change real accounts or monitor settings. Checks cover shell syntax, build failure propagation, Users buttons, standard-user password flow, password integrity, rename rejection, display positioning/failures/mirroring, keyboard variants, application overrides and quoting, wallpaper failures, live login, and package staging/installation arguments.

Run from Linux: `bash tests/regressions.sh`.

Run from this Windows checkout: `wsl -d Debian -- bash /mnt/d/1.Coding/LongProjects/OS/tests/regressions.sh`.

PowerShell syntax and the XML, policy and JSON configuration files also validated.

## Checks for the later combined update

The shell tests use mocks and are not a substitute for a boot test. WSL here is Debian 13; the target image is Debian 12. The next authorized image build should verify BIOS/UEFI boot, LightDM password authentication and user switching, actual monitor arrangements, graphical application launch, package installation, persistence across reboot, and Calamares installation in a disposable VM. Actual Docker/WSL builds and GitHub release uploads were not run.

This review does not establish that every bug is eliminated. The existing ISO in `out/` remains the earlier build and does not contain these source fixes.
