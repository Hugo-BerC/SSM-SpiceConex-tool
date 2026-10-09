<p align="center">
  <img src="sidebar_logo.png" alt="SSM-SpiceConex logo" width="460">
</p>

<h1 align="center">SSM-SpiceConex</h1>
<p align="center"><strong>AWS operations cockpit for SRE / Cloud Engineering</strong><br>EC2 · SSM · Tunneling · Commands · Performance · Connectivity · Certificates · Architecture</p>

---

<p align="center">
  <img src="skin_spiceconex.jpg" alt="Arrakis operations landscape" width="100%">
</p>

## What is it?

**SSM-SpiceConex** is a cross-platform PySide6 desktop utility for day-to-day AWS operations. It puts the common EC2 / Systems Manager workflows behind a single operator-oriented interface with an Arrakis-inspired visual language.

The architecture discovery module builds a deterministic AWS resource graph first, then projects it into a readable architecture view. The full inventory remains available for CSV export and later DR / dependency analysis.

## Current release

**Version:** `0.5.0-dev`

**Update channel:** `main` (Git)

> The in-app updater is intentionally conservative: it only accepts published semantic-version Git tags, refuses to overwrite local changes, prepares Python dependencies before changing source files, and restarts SpiceConex.

## Installation

The bootstrapper detects the host platform and installs the local tooling required by SpiceConex:

- Python 3.10+
- Git
- AWS CLI v2
- AWS Session Manager Plugin
- Python dependencies from `requirements.txt`
- `spiceconex` launcher

### Linux / Debian / Ubuntu / WSL / macOS

```sh
./setup.sh
```

The script uses `apt` on Debian/Ubuntu/WSL and Homebrew on macOS where required. On Linux it installs the launcher at `/usr/local/bin/spiceconex`.

For minimal Debian environments, the setup also installs `xfonts-base` when `xterm` is present so interactive sessions do not fail because of missing X11 fonts.

### Windows

Preferred:

```powershell
.\setup.bat
```

or:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\setup.ps1
```

The Windows bootstrapper keeps the Python environment under `%LOCALAPPDATA%\SSM-SpiceConex\venv` to avoid PySide6 / Windows long-path problems. It adds the application directory to the user's persistent `PATH`; open a **new terminal** after installation and run:

```text
spiceconex
```

If PowerShell reports that `setup.ps1` is not digitally signed, either unblock the downloaded script:

```powershell
Unblock-File .\setup.ps1
.\setup.ps1
```

or run it once with:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\setup.ps1
```

## Updating from GitHub

The application has a **CHECK FOR UPDATES** action in the sidebar.

For the updater to be enabled, the application must be running from a Git working tree with an `origin` remote pointing at the SpiceConex GitHub repository. The setup script installs Git automatically, but it does not create a Git repository from a ZIP distribution.

The update flow is:

```text
CHECK FOR UPDATES
       ↓
GitHub public semantic-version tags
       ↓
new version?
   ├── no → already up to date
   └── yes
        ↓
confirm
        ↓
git fetch --tags origin main
        ↓
install requirements from fetched revision
        ↓
git merge --ff-only refs/tags/vX.Y.Z
        ↓
restart SpiceConex
```

Local uncommitted changes are never overwritten automatically.

The updater uses GitHub's public API to discover tags, so it does not need a
GitHub token or SSH credentials to check for a release. If no version tag has
been published yet, the UI states this explicitly and makes no change. Publish
a release by creating an annotated tag such as `v0.5.0`; the tag must refer to
a commit reachable from the configured update branch. Update checks and network
work run outside the Qt UI thread. A short-lived lock prevents two update
processes from running concurrently; errors are recorded locally in
`update-error.log`.

## AWS authentication

SpiceConex uses the AWS CLI configuration already present on the workstation. SSO sessions are refreshed when an AWS operation reports an expired/unavailable session.

Typical first-time setup:

```sh
aws configure sso
aws sso login --profile <profile>

On Linux/Debian/WSL, SpiceConex runs `aws sso login --no-browser`, waits for the complete device authorization URL, and opens the URL containing `user_code` itself. This avoids opening only the base `/start/#/device` page with an empty code field. Keep the browser tab from the current attempt and complete it promptly; an `InvalidGrantException` usually means that authorization code expired or was already rejected. Close the old tab and retry Refresh to get a fresh code.

spiceconex
```

The application does not store AWS credentials, access keys, SSO tokens or IAM secrets.

## Interactive SSM terminals

Terminal launch is platform-aware:

- **Windows:** Windows Terminal → new tab → PowerShell.
- **macOS:** Terminal.app → new tab → the normal macOS `zsh` environment.
- **WSL:** Windows Terminal → new WSL tab when `wt.exe` is available.
- **Linux:** GNOME Terminal / Konsole / XFCE Terminal where available.
- **xterm fallback:** dark Arrakis palette, readable monospace font and clipboard-oriented configuration.

The AWS command itself remains the standard `aws ssm start-session` flow; the terminal emulator is only the interactive presentation layer.

## Features

| Module | Purpose |
|---|---|
| **Terminal** | EC2 inventory, selection and SSM sessions |
| **Tunneling** | SSM port forwarding |
| **Command** | AWS-RunShellScript / AWS-RunPowerShellScript |
| **Performance** | EBS / CloudWatch telemetry and baselines |
| **Connectivity** | Connectivity CSV validation and endpoint checks |
| **Certificates** | ACM inventory, load-balancer correlation and filtered CSV export |
| **Architecture** | Deterministic AWS discovery, resource inventory and architecture projection |

### Architecture discovery

```text
AWS APIs
   │
   ▼
Raw discovery
   │
   ├── Resource Inventory ──► CSV
   │
   └── Full Resource Graph
              │
              ▼
      Architecture Projection
              │
              ▼
       DR / dependency analysis
```

The architecture view deliberately hides low-level implementation resources such as ENIs, security groups, route tables and subnets when they do not improve architectural readability. The full graph remains available for forensic/resource-level inspection.

## Run

After installation:

```text
spiceconex
```

Demo mode:

```text
spiceconex --demo
```

Manual development run:

```sh
python -m pyside6_poc.main --demo
```

## Project structure

```text
app/                 Application configuration and update service
aws/                 AWS / SSO / session helpers
models/              Domain models
pyside6_poc/         PySide6 application UI
services/             SSM, certificates, discovery and performance services
terminal/             Cross-platform interactive terminal launcher
utils/                Logging and local paths
setup.sh              Unix / macOS / WSL bootstrapper
setup.ps1             Windows bootstrapper
setup.bat             Windows launcher for setup.ps1
requirements.txt      Runtime Python dependencies
```

## Version history

### 0.5.0-dev — current

- Cross-platform terminal launcher redesign.
- Windows Terminal sessions use new tabs where available.
- macOS sessions use Terminal.app and the normal `zsh` environment.
- WSL prefers Windows Terminal over `xterm`.
- xterm fallback receives an Arrakis dark palette and readable font configuration.
- Git installed automatically by the bootstrapper.
- In-app GitHub update workflow hardened around public semantic-version tags,
  clean-worktree checks, an out-of-process coordinator and dependency staging.
- Professional project README / installation documentation.

### 0.3.x

- Architecture discovery and scoped resource graph.
- Semantic architecture projection with AWS service icons.
- Architecture PNG export.
- Certificates and load-balancer inventory.
- AWS SSO recovery and session validation improvements.

## Security

See [`SECURITY.md`](SECURITY.md) for the security model and reporting guidance.

SpiceConex intentionally avoids storing AWS credentials and keeps environment-specific account/profile configuration outside the repository.

## Design direction

The UI follows a restrained **Arrakis / Dune editorial** direction: obsidian surfaces, sand typography, melange accents and minimal chrome. The goal is an operational tool rather than a generic sci-fi dashboard.

See [`DESIGN_DIRECTION.md`](DESIGN_DIRECTION.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md).

### Debian/Ubuntu and WSL: Qt system dependencies

`setup.sh` checks and installs missing native Qt/XCB packages (including `libxcb-cursor0`) using `apt-get`. This prevents the PySide6 `Could not load the Qt platform plugin "xcb"` startup error caused by missing XCB libraries. Re-run `./setup.sh` to repair an existing installation. This does not replace WSLg or an X/Wayland display server.
