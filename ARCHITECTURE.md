# Architecture

## Current migration slice

The PySide6 application is being migrated incrementally from the legacy Tkinter application. The first functional slice is now:

- **Terminal**: AWS profile/region discovery, EC2 fleet loading, STS identity validation and SSM interactive session launch.
- **Tunneling**: SSM `AWS-StartPortForwardingSession` command generation and cross-platform terminal launch, using the EC2 fleet loaded by Terminal.
- **Command**, **Performance** and **Connectivity** are implemented; their migrated workflows are described below.

AWS command construction is isolated in `services/ssm_commands.py` so UI code does not own shell quoting or SSM document details.

## UI and coordination modules

`pyside6_poc/main.py` assembles the main window and remaining operational pages.
Incremental extraction preserves the existing page behavior and import names:

- `theme.py` owns the palette, stylesheet and optional asset paths.
- `base_page.py` owns the common page shell and backdrop.
- `dialogs.py` owns shared confirmations and sanitized error dialogs.
- `performance_page.py` owns the Performance page; its AWS/analysis work remains in services.
- `startup.py` owns argument parsing, session logging and Qt startup; missing logos only omit the icon.
- `tasks.py` owns page worker lifecycle, context validity and task diagnostics.
- `aws/operations.py` shares one-time SSO recovery; the main-module adapter retains its existing callback seams.

The main module still exports the moved page/helper names for compatibility.
This is a gradual extraction, not a complete separation of every page. Domain
services remain independent of Qt. Atomic installer/updater JSON writes share
`app/installation.py`; platform runtime locations have one implementation.

`utils/redaction.py` sanitizes known sensitive patterns. Logging applies it after
formatting arguments and tracebacks, retaining bounded history with session and
task identifiers. The UI sanitizes error details and existing log content too.

## State flow

`Terminal` owns the loaded EC2 fleet and emits `instancesChanged`. `Tunneling` consumes that state without making a second EC2 API call.

`pyside6_poc/tasks.py` owns the background worker lifecycle for the operational
pages. Each page keeps a single task until its thread finishes. Results and errors
are delivered on the Qt UI thread only if the AWS context revision, profile and
region still match. A context change or explicit profile refresh invalidates
dependent inventories and selections; returning to the same profile does not
restore the validity of an older request. The main window refuses to close or
start an update while background threads remain active.

SSM command submission and invocation polling use recovery around each individual
AWS request. Polling credential recovery creates a fresh client and retains the
submitted command ID and completed targets, instead of repeating the operation.

`send_command(timeout_seconds=...)` bounds local tracking. Optional delivery and
execution timeouts configure the independent SSM limits; Connectivity supplies
both, with an execution budget based on the matrix size. `CommandResult` retains
its status labels and adds a defaulted `tracking_timed_out` flag so a local wait
limit is distinguishable from a terminal timeout reported by SSM. Available output
and the original command ID are retained without automatically cancelling AWS work.

## Cross-platform boundary

`terminal/platform.py` is responsible for launching the user's native terminal on Linux, macOS and Windows. AWS/SSM command construction is kept separate from terminal process launching.


## v6 migration scope

The PySide6 surface now contains the five operational areas: Terminal, Tunneling, Command, Performance and Connectivity.

- Terminal uses the shared EC2/STS services and preserves multi-target SSM sessions.
- Tunneling uses the existing SSM port-forwarding command builder.
- Command uses `AWS-RunShellScript` / `AWS-RunPowerShellScript` and polls invocation status without blocking the Qt UI thread.
- Performance migrates EBS attachment inspection, CloudWatch dashboard creation and the CSV baseline/microburst analysis.
- Connectivity validates TCP reachability from a managed instance through SSM Run Command.

The legacy Tkinter implementation is retained only as a migration reference in `ssm_spiceconex_legacy.py`. The canonical `ssm_spiceconex.py` entry point and `run.sh` both launch PySide6.


## EC2 inventory

The PySide6 application queries EC2 through boto3 directly using the selected profile and region. The AWS CLI remains used only where a native CLI process is required (for example SSM session/port-forward commands). This avoids parsing CLI JSON for the main inventory path and improves cross-platform behaviour.


## Architecture Discovery

The PySide6 application now includes an **Architecture** surface for deterministic AWS infrastructure discovery.

- `services/discovery/models.py` defines `ResourceNode`, `ResourceEdge` and `InfrastructureGraph`.
- `services/discovery/engine.py` collects regional VPC/network, EC2, ELBv2, RDS and Lambda resources plus account-global S3 and Route53 inventory.
- Relationships are derived from AWS API fields rather than shared VPC membership or naming conventions.
- `ib:resource:application` is the default application boundary. The Architecture UI can scope the graph using `ib:resource:application` or `ib:component:repo`, while retaining directly connected untagged/shared resources.
- `CURRENT REGION` uses the region selected in Terminal; `ALL REGIONS` enumerates enabled EC2 regions and discovers them without introducing a second region selector.
- The UI exposes both a resource inventory and an interactive topology map.

Application tags define **scope/ownership**; AWS API relationships define **topology**. This separation is intentional so a product map does not incorrectly treat every resource in the same VPC as a dependency.


## Architecture Discovery v1 patches

- Default scope tag: `ib:resource:application`.
- Alternative scope tag: `ib:component:repo`.
- Architecture map has semantic service lanes, VPC boundaries, relationship labels and a full-resource mode.
- AWS SSO recovery delegates browser launch to the CLI on Windows; Unix handles complete PKCE/device URLs from `aws sso login --no-browser`. A single deadline covers output and authorization waits, with bounded CLI cleanup. Raw authorization output is not logged. `aws/auth.py` shares authentication error classification and resolves SSO source profiles without running credential providers; permission/network failures retain their existing fallback or error path.
- SRE's independent `GlobalInventoryPage` uses a Qt table model/proxy to display multi-account aggregator resources without building the infrastructure graph. `services/global_inventory.py` handles paginated read-only Config APIs, optional Organizations account names, per-request SSO recovery, cooperative cancellation and atomic JSON/CSV exports. The report retains identifiers with unavailable details, source update status and per-type coverage counts. Its profile, aggregator region and name are independent of Terminal/Architecture selections. It does not assert full organization/resource coverage or enable AWS recording.
- AWS SDK configuration is centralized in `aws/session.py`; `aws/errors.py` supplies shared error categories while `aws/auth.py` controls credential recovery. Read operations have bounded standard retries; SSM transport retries are disabled to avoid duplicate commands. Partial certificate/tag inventories expose warnings instead of appearing complete.
- Performance uses page-owned tasks for EBS reads, baseline analysis and confirmed dashboard publication. Dashboard names retain full instance identity and a random suffix; existing names are refused before writing. CloudWatch does not provide an atomic create-if-absent dashboard API.
- Updates verify origin, approved SHA and fast-forward ancestry before staging a versioned venv. The detached coordinator preserves the old interpreter, records a recovery journal and activates the new interpreter through `app/runtime.py` only after source merge. Recovery uses `reset --keep` and stops if local edits prevent a safe restore. Interrupted transactions require journal review before clearing a stale lock; versioned environments are not automatically deleted.
- Terminal profile/account selector supports case-insensitive search/autocomplete.
- Default region is `eu-west-1`.
- Certificates exports the currently filtered Certificates and ALB/NLB tables independently to CSV.
