<p align="center">
  <img src="sidebar_logo.png" alt="SpiceConex logo" width="460">
</p>

<h1 align="center">SpiceConex</h1>
<p align="center"><strong>AWS operations cockpit for SRE / Cloud Engineering</strong><br>EC2 · SSM · Tunneling · Commands · Performance · Connectivity · Certificates · Route 53 · CloudFront · Architecture</p>

---

<p align="center">
  <img src="skin_spiceconex.jpg" alt="Arrakis operations landscape" width="100%">
</p>

## What is it?

**SpiceConex** is a cross-platform PySide6 desktop utility for day-to-day AWS operations. It puts the common EC2 / Systems Manager workflows behind a single operator-oriented interface with an Arrakis-inspired visual language.

The architecture discovery module builds a deterministic AWS resource graph first, then projects it into a readable architecture view. The full inventory remains available for CSV export and later DR / dependency analysis.

## Current release

**Version:** `1.0.0`

**Update channel:** `main` (Git)

> The in-app updater verifies the expected GitHub repository and approved commit SHA, checks fast-forward ancestry before preparing dependencies, validates a separate Python environment and retains the previous environment for recovery.

## Installation
The bootstrapper detects the host platform and installs the local tooling required by SpiceConex:

- 64-bit Python 3.10+
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

AWS CLI installation uses the official architecture-specific ZIP on Linux/WSL
and the official PKG on macOS. Unsupported processor architectures are rejected
explicitly rather than downloading an incompatible executable.

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

The Windows bootstrapper keeps the Python environment under `%LOCALAPPDATA%\SSM-SpiceConex\venv` to avoid PySide6 / Windows long-path problems. It adds `%LOCALAPPDATA%\SSM-SpiceConex\bin` to the user's persistent `PATH`; open a **new terminal** after installation and run:

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

Both installers check dependencies with `pip check` and import the application
before registering the installation. Registration is atomic and shared with the
updater: Windows uses `%LOCALAPPDATA%\SSM-SpiceConex`, macOS uses
`~/Library/Application Support/SSM-SpiceConex`, and Linux uses
`${XDG_CONFIG_HOME:-~/.config}/SSM-SpiceConex`. Re-running setup selects the
validated setup interpreter, replacing any earlier updater selection. Setup refuses
to run while an update/recovery lock exists; review that transaction first.

`requirements.txt` is the single source of runtime dependencies; package metadata
loads that same file. The application version comes from `app/version.py`.
Missing optional logos/background artwork do not prevent startup. Invalid or
non-positive `SSM_SPICECONEX_AWS_TIMEOUT` values use the 20-second default.

## Updating from GitHub

Release notes are available in [RELEASE_NOTES.md](RELEASE_NOTES.md). Pushing a
version tag runs the Windows/macOS/Linux CI matrix before GitHub Actions creates
the corresponding GitHub release using its temporary repository token.

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
verify origin + fetch selected tag and branch
        ↓
verify approved SHA + branch ancestry + fast-forward
        ↓
prepare separate venv + pip check + UI import check
        ↓
git merge --ff-only <verified SHA>
        ↓
activate validated Python runtime
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

HTTPS and SSH GitHub remotes are accepted only when they identify the configured
repository. A tag that moves after approval, a divergent local revision or a dirty
working tree is rejected. The coordinator checks the working tree and HEAD again
after preparing dependencies. Local changes are never forcibly reset.

Dependency preparation uses a new environment at its final path under the per-user
runtime directory (`SSM-SpiceConex/runtimes/<installation>/versions/<transaction>`).
It does not run pip against the active environment or move an existing venv.
Package destination settings from pip configuration are disabled for preparation;
index/proxy/certificate environment settings remain available. A small runtime
selector in `ssm_spiceconex.py` lets existing launchers use the validated interpreter.
Older releases without that selector require installation through setup.

Each transaction retains `transaction.json` with the previous/target SHA, previous
interpreter and phase; `active.json` selects the current interpreter. If merge or
activation fails, recovery restores the previous selection and uses Git's
`reset --keep` only for the revision changed by the updater. If concurrent edits
prevent recovery, it stops without overwriting them or automatically restarting,
and retains the launch lock with journal phase `recovery_required`.
The journal and both environments remain available for manual recovery. After a
process crash or power loss, review the journal before removing a stale update
lock; launch is blocked while that lock exists. Previous environments are retained
and require deliberate cleanup after an update has been verified.

## AWS authentication

SpiceConex uses the AWS CLI configuration already present on the workstation. SSO sessions are refreshed when an AWS operation reports an expired/unavailable session.

Profile discovery reads `[default]` and `[profile NAME]` sections from AWS config,
plus named sections from the shared credentials file. SSO session and service
configuration sections are not offered as profiles. Local profiles remain
available when AWS CLI profile enumeration fails.

Typical first-time setup:

```sh
aws configure sso
aws sso login --profile <profile>
spiceconex
```

Windows delegates browser opening to AWS CLI. Linux, macOS and WSL run
`aws sso login --no-browser` and open the complete authorization URL themselves.
Both PKCE and device authorization are supported, including legacy device URLs
and the CLI's newer autofill links. Incomplete URL lines and device pages without
a code are not opened. PKCE URLs must be opened on the same machine as the CLI;
see the [AWS CLI SSO authentication guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).

To explicitly request device authorization with a supporting AWS CLI v2, set
`SSM_SPICECONEX_SSO_DEVICE_CODE=1` in the environment before launching SpiceConex.
Otherwise, the CLI's default flow is retained. This does not rewrite AWS profiles.
If a browser cannot be opened, run `aws sso login --profile <profile>` manually;
add `--use-device-code` when authorizing from another device.

One 15-minute authorization deadline covers CLI startup, output handling and the
authorization wait. Expiry terminates the owned CLI process, with a bounded
two-second cleanup. No raw CLI output, authorization URLs, one-time codes or tokens
are written to SpiceConex logs or error messages. An `InvalidGrantException`
indicates an expired or rejected attempt: close the old tab and retry for a fresh
authorization.

Automatic recovery retries an authentication failure once, only for configured
SSO profiles or role chains backed by an SSO `source_profile`. The source profile
is logged in while the AWS operation retains its original selected profile.
Permission, network and non-SSO credential errors are returned without starting
an unrelated SSO login.

The application does not store AWS credentials, access keys, SSO tokens or IAM secrets.

The in-app **Configure profiles** workflow uses bootstrap credentials only in memory to query AWS Organizations. It merges generated `sso-session` profiles into the selected AWS config file, preserves unrelated profiles and never writes to the AWS shared credentials file.

## Interactive SSM terminals

Terminal launch is platform-aware:

- **Windows:** Windows Terminal → new tab → PowerShell.
- **macOS:** Terminal.app → new tab → the normal macOS `zsh` environment.
- **WSL:** Windows Terminal → new WSL tab when `wt.exe` is available.
- **Linux:** GNOME Terminal / Konsole / XFCE Terminal where available.
- **xterm fallback:** dark Arrakis palette, readable monospace font and clipboard-oriented configuration.

The AWS command itself remains the standard `aws ssm start-session` flow; the terminal emulator is only the interactive presentation layer.

Arguments are quoted for the selected shell, including profiles containing spaces,
quotes or shell metacharacters. Windows uses Windows PowerShell's encoded-command
input; macOS command and title strings are escaped for AppleScript separately
from the shell command. WSL also escapes Windows Terminal's command separator.

## Features

| Module | Purpose |
|---|---|
| **Terminal** | EC2 inventory, selection and SSM sessions |
| **Tunneling** | SSM port forwarding |
| **Command** | AWS-RunShellScript / AWS-RunPowerShellScript |
| **Performance** | EBS / CloudWatch telemetry and baselines |
| **Connectivity** | Connectivity CSV validation and endpoint checks |
| **Certificates** | ACM inventory, load-balancer correlation and filtered CSV export |
| **Route 53** | Private hosted-zone selector, DNS record discovery, per-column filters and CSV export |
| **CloudFront** | Global distribution inventory with delivery, origin, WAF, protocol and configuration filters |
| **Architecture** | Deterministic AWS discovery, resource inventory and architecture projection |
| **GlobalInventory (SRE)** | Multi-account AWS Config aggregator inventory, coverage evidence and JSON/CSV export |

### Operational safeguards

- Run Command requires an explicit confirmation showing the AWS context, selected instances and command. A single operation is limited to 50 targets and is submitted as one auditable SSM command.
- Run Command and connectivity validation recover expired SSO credentials per AWS request. A command already submitted is not resent when credentials expire during result polling; tracking keeps the original command ID.
- Changing or refreshing the AWS profile/region clears account-bound inventories and selections. Results from an earlier context are discarded, including when switching away and back to the same profile.
- Each operational page accepts one background task at a time. Closing or updating SpiceConex is blocked while background operations are active. Changing context does not cancel commands already submitted to AWS.
- Every CSV export is protected against spreadsheet formula injection while preserving the visible values in the application.
- Long AWS validation and discovery operations run outside the Qt interface thread so the UI remains responsive while SSO or AWS APIs are resolving.
- An SSM local tracking timeout does not mean that the remote command has stopped. The application retains the command ID and any output already returned, explicitly reports the unknown remote status, and does not cancel the command automatically.

### Connectivity validation

Connectivity accepts TCP destinations only. The CSV requires service, destination,
port and scope columns (the existing Spanish/English aliases remain supported).
An omitted or empty protocol defaults to TCP; unsupported protocols, empty required
cells and ports outside 1–65535 are rejected before submitting a command.

The selected timeout applies to each destination, from 1 to 300 seconds. The Linux
managed source instance needs `nc` or `ncat`; GNU `timeout`, when available, also bounds each invocation
including name resolution. The overall execution and polling budgets scale with
the number of rows. Matrices exceeding SSM's 48-hour execution limit must be split.
Missing or truncated results appear as `NO_RESULT`, or `UNKNOWN` when local tracking
expires. They are counted separately from network failures; partial reports retain
the SSM command ID. CSV/table columns remain unchanged.

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

AWS Config discovery combines its recorded-resource inventory and batch configuration
API with optional Advanced Query enrichment for tags and relationships. If enrichment
is denied or incomplete, available resources remain visible with warnings; SpiceConex
does not change IAM permissions or enable a recorder. Some recorded resource types
are not queryable and can lack this additional metadata. ACM discovery explicitly
requests supported RSA and ECDSA key algorithms instead of relying on the API's
default key-type filter.

### Performance safety

EBS queries, CSV baseline analysis and CloudWatch publication run in background
tasks. Changing instance/account/region invalidates old EBS results. Cancelling a
read or analysis discards its result after the in-flight operation finishes.
Cancelling the publication confirmation performs no AWS calls. Once a confirmed
publication starts, it is not presented as cancellable or reversible.

After loading volumes, Performance proposes an editable dashboard name containing
the full sanitized instance name, region, instance ID and a random suffix. The
confirmation shows that exact name, profile, region, instance and volume IDs.
Publishing checks `cloudwatch:GetDashboard` and refuses an existing name; inability
to check permissions also blocks the write. It then calls `cloudwatch:PutDashboard`.
No IAM permissions are changed automatically. CloudWatch has no conditional
create-only dashboard API, so an external writer racing the existence check cannot
be ruled out; keep the proposed random suffix to minimize that risk. Dashboard
validation messages report that the write may already have succeeded.

### AWS clients and partial inventories

SDK clients share `aws.session.client_config()`: a five-second connection timeout,
a read timeout of at least five seconds (default 20, configurable with
`SSM_SPICECONEX_AWS_TIMEOUT`), TCP keep-alive and standard retries with three total
attempts. SSM uses one transport attempt to avoid replaying an ambiguous
`SendCommand`; credential recovery remains per request. Sessions remain local to
each operation/thread and use the existing AWS credential provider chain.

Authentication, permission, throttling and network failures are classified
consistently. Credential errors escape partial-inventory fallbacks for SSO
recovery. Certificates and Architecture display partial-inventory warnings when
details, tags or service inventories cannot be read. An untagged S3 bucket is not
treated as a failed tag read.

### GlobalInventory

Open **SRE → GlobalInventory**, select the AWS profile for the account hosting
your Config aggregator (for example IB), and select the **aggregator's region**.
Choose **LOAD AGGREGATORS** or enter its name directly (the initial suggestion is
`all_accounts`), then **DISCOVER**. The region locates the aggregator; discovery
includes all source accounts, regions and resource types exposed by that
aggregator. These controls are independent of the Terminal and Architecture scope.
No map or resource relationships are built for this view.

The read-only collector paginates resource counts, identifiers and source status,
then reads details in batches of up to 100 with bounded retries for unprocessed
identifiers. Original AWS IDs and types are preserved; the resource key combines
account, source region, type and ID to prevent collisions. ARN, name and Config
capture time are included when available. Account names are optionally resolved
through Organizations; failure to list accounts leaves names empty without
discarding the inventory. A paginated Advanced Query selects tag metadata from
the same aggregator to resolve the **Name** tag (case-sensitive). That tag takes
precedence over the native resource name; for EC2 instances this supplies the
console's Name value. If no Name tag is available, the native Config resource
name is retained. If neither exists, Resource Name stays empty, without copying
the ID or ARN. Tag data reflects what Config recorded, not a live EC2 lookup;
unrecorded or non-queryable resources can lack this enrichment. Failures are
shown under `coverage.name_tags_status` and warnings without discarding resources.
The query is streamed by page, adds no per-resource API calls to source accounts,
and can lengthen discovery for large inventories. Full tags/configurations are
not exported.

Filter by account, region, resource type or resource name/ID/ARN. **ALL RESOURCES**
is the default export scope; **FILTERED RESOURCES** exports only the current view.
CSV contains one resource per row in the same default column order as the table,
without the internal `resource_key` JSON list. If Config supplies an ARN as its ID,
it is shown in the ARN column and Resource ID is left empty rather than inventing
a short ID. If that identifier ARN differs from the detailed ARN (for example a
Security Hub subscription versus its standard), both are retained under `arn`
and `config_resource_arn` (Config Identifier ARN). Native IDs and resource names
can legitimately be equal and retain their original meaning.

The **CSV SEPARATOR** selector defaults to **Excel (;)** for semicolon-based
spreadsheet locales; choose **CSV (,)** for comma-based importers. CSV uses UTF-8
with BOM, proper quoting and spreadsheet formula protection. In Excel, use
**Data → From Text/CSV**, select the matching delimiter and import account IDs as
text to preserve leading zeros. The CSV does not add a nonstandard `sep=` line.
JSON schema **2.0** uses the same separated resource fields as the table and CSV:
ARN values occur only in `arn` or `config_resource_arn`, never in Resource ID or
Resource Name. Internal list keys are omitted from exported resource rows. This
replaces the raw identifiers in schema 1.0; original identifier ARNs remain
available in the appropriate ARN field. The JSON report also
contains the schema version, collection interval, source configuration, summaries,
coverage, warnings and explicit export scope/counts. Keep the JSON as evidence
alongside CSV files used to compare against a ServiceNow CMDB export. Export
timestamps and Config capture times are separate; collection is not a simultaneous
snapshot of all accounts.

**COVERAGE / WARNINGS** exposes source status (`SUCCEEDED`, `FAILED`, `OUTDATED`),
per-type expected/listed/detail counts and any missing metadata. `COLLECTED` means
the collector finished reading the available aggregator inventory, not that all
AWS resources or organization accounts are recorded. Config recording settings,
aggregator filters and propagation delays can limit coverage. A successful source
update does not establish per-resource freshness. Types without a native collector
in Architecture remain eligible for this inventory. No recording settings,
aggregators or IAM permissions are changed.

The status also shows collected versus expected resources, using the aggregator's
count before listing, and remains visible after export. If count pagination fails,
the reported total is retained when available; otherwise the expected total is
explicitly unknown. Counts can change during discovery and are not an atomic
snapshot. A mismatch produces a partial-report warning rather than silently
claiming completion. This diagnostic does not retrospectively explain differences
between older exports without their coverage metadata.

Partial failures retain discovered identifiers and mark missing details explicitly.
**CANCEL** stops between AWS requests and retains the partial report; an in-flight
request, SDK retry or SSO login must finish first. Cancelled and empty reports can
still be exported as JSON evidence. Reads and exports run outside the Qt UI thread.
Required read operations are `config:DescribeConfigurationAggregators`,
`config:DescribeConfigurationAggregatorSourcesStatus`,
`config:GetAggregateDiscoveredResourceCounts`,
`config:ListAggregateDiscoveredResources` and
`config:BatchGetAggregateResourceConfig`; Name tag enrichment additionally uses
`config:SelectAggregateResourceConfig`. `organizations:ListAccounts` is optional.
The application uses the existing profile/SSO flow and does not grant permissions.

### DR dashboard (first version)

Select **SELECTED REGIONS** in Architecture, choose the source and recovery regions, and run **DISCOVER** once. The map, resource inventory and **DR DASHBOARD** share that discovery and the application filter. In the DR tab, select API Gateway resources in the source and DR regions, then choose **ADD PAIR FROM SELECTED**. This explicit pair supports different API names in each region. Prepare metrics for one or more pairs, then export the evidence as self-contained HTML, CSV, PNG or the dashboard definition as JSON. The confirmed CloudWatch action creates or replaces only the explicitly named `{profile}-Disaster_Recovery` dashboard.

The DR dashboard presents a curated set of architectural resources that have supported CloudWatch metric dimensions, including API Gateway, EC2, EBS, ALB/NLB/GWLB, ECS, RDS, Lambda, DynamoDB, EFS, FSx, ElastiCache, Auto Scaling and EKS Container Insights. Structural inventory such as VPCs, subnets, ENIs and security groups remains in the inventory and architecture views. API Gateway is the external API traffic boundary; load-balancer counters may include health checks or service-to-service traffic and are labelled as infrastructure evidence. SpiceConex does not enable access logs, detailed monitoring or Container Insights. Missing metrics remain `NO DATA`; they are never converted to zero. CloudWatch API requests and custom dashboards may incur AWS charges.

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

## Validation and diagnostics

Run the regression suite from the repository root after installing dependencies:

```sh
python -B tools/run_tests.py
```

The runner uses offscreen Qt, a temporary log directory, mocked icon downloads
and blocked Python socket connections. AWS integrations use mocks/stubs; no real
AWS infrastructure is required. Use `--pattern test_startup.py` (for example) to
run a focused subset.

Local validation for this change used Windows x64 / Python 3.14, including a fresh
virtual environment, wheel build/install, `pip check`, console entry point and
real demo window startup without logos. The CI workflow defines Windows, Linux
and macOS jobs with Python 3.10, 3.12 and 3.14. Those CI jobs and native macOS/Linux
installer runs have not been executed locally; compatibility on those combinations
remains to be validated. Installing OS tooling through the bootstrap scripts was
not exercised by the isolated clean-environment test.

Logs retain history across restarts, with a session marker and background-task
identifiers/durations. Rotation retains the current 2 MiB file and up to five
backups. `SSM_SPICECONEX_LOG_DIR` overrides the log directory. If the normal log
location is unavailable, logging falls back to a temporary location or stderr.
Known credential/token fields, authorization URLs and authentication headers are
redacted after message/traceback formatting and in the log viewer. This is a
defence in depth measure: do not deliberately log secrets or share infrastructure
exports without reviewing them.

## Project structure

```text
app/                 Application configuration and update service
aws/                 AWS / SSO / session helpers
models/              Domain models
pyside6_poc/         PySide6 application UI
services/             SSM, certificates, Route 53, discovery and performance services
terminal/             Cross-platform interactive terminal launcher
utils/                Logging and local paths
tests/                Mocked AWS and UI regression tests
tools/run_tests.py    Isolated offline regression runner
setup.sh              Unix / macOS / WSL bootstrapper
setup.ps1             Windows bootstrapper
setup.bat             Windows launcher for setup.ps1
requirements.txt      Runtime Python dependencies
```

## Version history

### 1.0.0 — current

- SRE GlobalInventory with multi-account Config inventory and JSON/CSV exports.
- Hardened updates, asynchronous Performance operations and shared AWS errors.
- Aligned dependency metadata, installer registration and optional-asset startup.
- Extracted UI/task/authentication components and retained redacted diagnostic history.

### 0.5.0

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
