# Updating doshy OS without wiping the drive

The next combined OS image includes **Settings → System updates**. Once that version is installed, future desktop and system-tool updates can come from your own public GitHub repository. The updater does not format drives, rewrite partitions or replace home directories.

The default update source is [josh2000ukb-commits/DoshyOS-OG](https://github.com/josh2000ukb-commits/DoshyOS-OG). It is included in the rebuilt image. You can change it later in System updates.

## Set up the OS once

1. Use the configured public GitHub repository, including this project�s `.github` directory. Leave the generated ISO and `out/` files out of Git.
2. On doshy OS, open **Settings → System updates → Set GitHub repository**.
3. Enter `yourname/your-repository`, or its `https://github.com/...` URL, and authenticate as an administrator.
4. Use **Check for updates** when a newer release is available.

You can also fill in `repository` in `config/includes.chroot/etc/doshy/update.json` before the combined image is built. Later updates deliberately preserve this setting rather than replacing it with the publisher's default.

The desktop checks after login, at most once per day per user. It stays quiet if the source is unset, the network is unavailable or no update exists. When an update exists, it offers **Install update** or **Later**. It never installs or restarts the computer automatically.

## Publish each future update

The starting version is `1.0.0`. For the first future release:

1. Make your changes in `config/includes.chroot/`.
2. Change `config/includes.chroot/usr/share/doshy/version` to `1.0.1`.
3. If the change needs new Debian packages, add their package names to `updates/packages.txt`. Keep earlier entries so people can skip releases.
4. Commit and push the changes, then push the matching tag:

   ```sh
   git add .
   git commit -m "Release doshy OS 1.0.1"
   git push
   git tag v1.0.1
   git push origin v1.0.1
   ```

The **Publish OS update** workflow tests the updater in Debian 12, builds the cumulative update and publishes these release assets:

- `doshy-update.tar.gz`
- `doshy-update.tar.gz.sha256`

The tag and version file must match. Use new increasing `MAJOR.MINOR.PATCH` versions, not prereleases or a reused tag. The OS checks the latest stable release and only offers a version greater than the installed version. Every bundle includes all managed doshy files, so installing intermediate versions is unnecessary.

Pushing ordinary source commits alone does **not** publish an installable update. The release tag publishes it. The existing ISO workflow still builds on main/master pushes or manual dispatch; update tags run the much smaller updater workflow instead of building an ISO.

To prepare an update manually without an ISO build:

```sh
python3 scripts/build-update.py
```

On Windows, use `python scripts/build-update.py`. The files appear in `out/updates/`. Upload **both** files to a matching stable GitHub Release. A manually dispatched updater workflow also provides these files as an Actions artifact, but only tag runs publish a release automatically.

## What updates cover

Bundles carry the existing doshy-managed launchers, shell/Python tools, menus, wallpaper assets, desktop defaults, LightDM defaults, policy and doshy systemd unit files. Required Debian packages come from the machine's configured APT repositories. Package removal is disabled and APT keeps existing conffiles.

Build scripts, ISO boot menus, arbitrary build hooks and Discord's build-time download hook are not executed during an in-place update. Changes to package lists alone affect future ISOs; add runtime dependencies to `updates/packages.txt` as described above.

This first updater supports the existing Debian 12 amd64 base. It does not replace a live USB's kernel/initramfs, bootloader or squashfs image, and it does not perform Debian major-version upgrades. Boot-image updates require a separate design because a live USB boots those files outside its writable root overlay. Full installations can continue using Debian's normal package tools for kernel/security maintenance.

## Persistence, local changes and recovery

For a live USB, **full persistence** must already work. The updater checks that the writable root overlay is on a persistent filesystem and refuses a RAM-only session. Debian's setup uses a persistence partition containing `persistence.conf` with `/ union`; a home-only persistent partition is insufficient. [Debian documents this requirement here](https://live-team.pages.debian.net/live-manual/html/live-manual/customizing-run-time-behaviours.en.html).

Full installations do not need a persistence partition.

The combined image records hashes of shipped doshy files after build hooks run. Each update compares the installed files against that baseline or the previous release:

- Unchanged managed files are updated; unchanged obsolete managed files are removed.
- Locally changed or deleted managed files are preserved. Incoming replacements are saved under `/var/lib/doshy-update/conflicts/VERSION/`, and the completion dialog lists the affected paths. These may need review before every new feature works as intended.
- Files in users' home directories, account databases and the configured update source are outside the bundle's writable paths.

Before replacing files, the updater makes backups and writes a recovery journal. A failed file update restores its previous files and version. After an interrupted update, the recovery service restores them at the next boot before the desktop starts. Keep the drive connected while updating; recovery cannot repair filesystem or hardware damage.

**Restore previous update** restores the last successful set of managed-file changes. It refuses to overwrite changes made after that update. Newly installed/upgraded Debian packages remain installed: file rollback is not an APT or whole-disk snapshot. Backups are retained under `/var/lib/doshy-update/backups/`; account for their storage on small USB drives.

Administrator authentication is required to install, restore or change the trusted source. Downloads use GitHub HTTPS, require release assets from the configured repository/tag, and verify SHA-256 plus every file in the manifest. Archives containing links, traversal paths, unlisted files, unsupported modes or unsupported boot packages are rejected. The repository is the trust boundary; checksums detect damaged/mismatched downloads, not a compromised publisher account. This uses GitHub's [latest-release API and release assets](https://docs.github.com/en/rest/releases/releases).

## Testing and current status

```sh
python3 -B -m unittest discover -s tests -p test_update.py -v
bash tests/regressions.sh
```

All 31 updater tests passed in Debian WSL, and the existing 18 shell regression checks passed. Tests cover the complete apply flow with mocked GitHub/APT, checksums, archive validation, persistence detection, file conflicts, interrupted-write recovery, rollback, concurrent-update locking and bundle generation. Python was installed in the WSL test environment to run the Linux-specific checks.

A local `1.0.0` bundle was generated and validated. No ISO was rebuilt or modified, and no actual GitHub release was published. The old ISO does not yet contain this feature. The combined image still needs a disposable VM/USB test for the graphical dialogs, authentication, real persistence across reboot and a real release download once your repository exists.
