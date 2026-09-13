# DoshyOS-OG Planning

This file contains ideas and requirements for future DoshyOS-OG development.
Planning notes are recorded here only; no implementation should be made unless
the owner explicitly authorizes changes.

## Ideas

### 1. Change the main screen

- Goal: allow the user to choose which connected physical monitor is the DoshyOS
  primary/main screen.
- The selected main screen should receive the bottom bar's primary controls,
  including the Start menu, internet/network status, volume controls, clock, and
  other system-tray items.
- Applications launched from DoshyOS should open on the selected main screen by
  default.
- Bug fix: the user's screen layout did not appear to save between sessions. The
  monitor arrangement, selected main screen, resolution, refresh rate, and related
  display settings should persist after logout, reboot, and shutdown/startup.
- Implemented for 1.0.2: main-screen chooser, full bottom bar on the main screen,
  new normal application windows on that screen, confirmed per-user monitor profiles,
  login/reconnection restore, resolution, refresh rate, rotation and arrangement.
- Decision: existing windows stay in place; new normal windows use the chosen main
  screen even when an application remembers a previous position. Dialogs retain
  their association with the parent application. Live USBs need persistence.

### 2. Pin and unpin applications

- Goal: allow applications to be pinned and unpinned from the bottom bar and the
  pop-out Start menu.
- Current status: partially present. The bottom bar already has fixed launchers for
  Files, Terminal, Firefox, and Discord. The Start menu already exposes those
  applications and includes a generated Applications list.
- Missing: user-controlled pinning/unpinning and persistence of each user's choices.

### 3. Uninstall and search applications

- Goal: add application uninstalling and application search in the Applications tab.
- Current status: partially present. The Applications tab already discovers installed
  graphical applications and sorts them by name. The OS can also install `.deb`
  packages. User-account removal exists, but application removal does not.
- Missing: application search/filtering and a safe administrator-authorized
  uninstall flow that handles dependencies and protects essential system packages.

### 4. Antivirus and malware protection

- Microsoft Defender has been removed from the plan at the owner's request.
- Goal: investigate a suitable Linux antivirus or malware-protection option for
  DoshyOS.
- Candidate: ClamAV is free and open source. Its Linux `clamonacc` component can
  provide on-access scanning through `clamd`, while `clamscan` supports manual scans.
- Candidate: Sophos Protection for Linux provides stronger managed endpoint features,
  but requires an appropriate license and management setup.
- Important design questions: live-USB performance, signature updates, offline use,
  desktop notifications, quarantine behavior, administrator permissions, and whether
  protection should be built in or offered as an optional package.

### 5. Applications that open at startup

- Goal: let the user choose applications that launch automatically when they sign in.
- Current status: partially present. Openbox already runs a fixed startup script for
  keyboard restoration, wallpaper, Tint2, network and volume tools, PolicyKit, user
  directories, and the update check.
- Missing: a user-facing startup-app settings screen, enable/disable controls,
  per-user saved choices, and safeguards against invalid or repeatedly failing
  commands.

### 6. Desktop application icons

- Goal: let the user add desktop icons for applications, with a click opening the
  selected application.
- Current status: not present as a user feature. Applications are currently opened
  from the bottom-bar launchers, Start menu, or Applications list.
- Missing: desktop icon creation/removal, application shortcuts based on `.desktop`
  files, icon positioning, persistence across sessions, and a safe way to prevent
  arbitrary commands or untrusted desktop files from being launched accidentally.

### 7. Custom startup and login experience

- Goal: replace the standard Debian Linux login experience with a custom DoshyOS
  startup and login flow.
- Startup sequence requested:
  1. Show a blue startup screen with a slowly pulsing DoshyOS logo.
  2. Transition to a home/idle screen showing the time and an attractive background.
  3. When the user presses a key or button, blur the background and reveal the login
     screen.
  4. After successful login, continue to the DoshyOS desktop.
- Current status: not implemented. The OS currently uses LightDM with its configured
  GTK greeter, plus GRUB/Syslinux boot menus before the display manager starts.
- Design questions: logo artwork and animation style, idle-screen clock format,
  background asset, accessibility and keyboard navigation, multi-monitor behavior,
  lock-screen behavior, power/restart controls, failed-login handling, and whether
  the transition should remain lightweight enough for older USB hardware.

### 8. Graphics driver installer and updater

- Goal: provide an easy-to-use program for installing and updating graphics drivers.
- The tool should detect the user's graphics hardware and clearly explain available
  driver options before installing or updating anything.
- It should support common hardware paths, especially Intel, AMD, and NVIDIA, while
  avoiding driver changes that could leave the user without a working display.
- Implemented for 1.0.2: hardware/active-driver detection, signed Debian 12 sources
  including firmware/non-free, package previews, administrator authentication,
  update/install status, restart guidance and recovery help.
- Limits: persistent live USBs support userspace/firmware updates, not proprietary
  NVIDIA or boot-kernel replacement. Proprietary NVIDIA requires a supported Debian
  recommendation, full installation and Secure Boot off. No automatic package
  rollback; previous versions and transaction logs are recorded for recovery.

## Requirements and decisions

Record confirmed requirements, design decisions, and constraints here.

- Do not implement any idea until the owner explicitly authorizes development.
- The owner authorized implementation, testing and GitHub publication of #1 and #8
  as version 1.0.2. Other ideas remain planning-only until separately authorized.

## Open questions

Record questions that need answers before implementation here.

## Future releases

Track ideas by target release when a release has been chosen.

- 1.0.2: #1 (main screen and saved layouts) and #8 (graphics driver manager).
- 1.0.3: multi-monitor swap/layout bugfix; larger screens must retain correct
  framebuffer sizing and mouse coordinates when their side changes.
