# Architecture

## Current migration slice

The PySide6 application is being migrated incrementally from the legacy Tkinter application. The first functional slice is now:

- **Terminal**: AWS profile/region discovery, EC2 fleet loading, STS identity validation and SSM interactive session launch.
- **Tunneling**: SSM `AWS-StartPortForwardingSession` command generation and cross-platform terminal launch, using the EC2 fleet loaded by Terminal.
- **Command**, **Performance** and **Connectivity** remain placeholders until their corresponding legacy workflows are migrated.

AWS command construction is isolated in `services/ssm_commands.py` so UI code does not own shell quoting or SSM document details.

## State flow

`Terminal` owns the loaded EC2 fleet and emits `instancesChanged`. `Tunneling` consumes that state without making a second EC2 API call.

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
- AWS SSO session recovery delegates browser launch to `aws sso login --profile ...` so AWS CLI can open the complete authorization URL with the device code when supported. Avoid manually opening only the base `/start/#/device` URL, which can leave the code-entry page blank.
- Terminal profile/account selector supports case-insensitive search/autocomplete.
- Default region is `eu-west-1`.
- Certificates exports the currently filtered Certificates and ALB/NLB tables independently to CSV.
