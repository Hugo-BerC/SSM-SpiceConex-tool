# SSM-PowerConnect for AmazonSpiceOx

AmazonSpiceOx variant of SSM-PowerConnect.

This version keeps the existing Tkinter workflow but removes Windows Terminal, WSL Debian and macOS Terminal dependencies. Interactive SSM sessions and port forwarding are opened through a Linux graphical terminal, preferably `xterm`.

## Runtime Requirements

- AmazonSpiceOx with the GUI profile enabled
- Python 3 with Tkinter
- AWS CLI
- AWS Session Manager plugin
- `xterm`
- Python packages from `requirements.txt`
- AWS SSO profiles configured in `~/.aws/config`

## Install Python Dependencies

Inside AmazonSpiceOx:

```sh
cd /path/to/SSM-PowerConnect/AmazonSpiceOx
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

If the distro image already includes these Python packages, the virtualenv is optional.

## Run

From a graphical AmazonSpiceOx session:

```sh
sh run.sh
```

If you launch it from a plain shell without `DISPLAY`, use the distro GUI wrapper:

```sh
python-gui AmazonSpiceOx/ssm_powerconnect.py
```

The default AWS region is read from `AWS_REGION` or `AWS_DEFAULT_REGION`; if neither is set, it falls back to `eu-west-1`.

## AWS Flow

```sh
aws configure sso
aws sso login --profile <profile>
sh run.sh
```

Use **PowerCon** to discover instances and open SSM sessions, **PowerTunnel** for port forwarding, **PowerCommand** to run SSM commands, and **PowerEBS** for EBS dashboard and CSV analysis.
