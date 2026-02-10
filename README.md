# SSM-PowerConnect

## Overview
SSM-PowerConnect is a Tkinter GUI for AWS SSM operations. It lets you list EC2 instances, open SSM sessions, create port-forwarding tunnels, run commands, and analyze EBS performance/CSV data from a single interface.

## Features
- Instance discovery by AWS profile
- SSM session connect
- SSM port forwarding
- Multi-instance command execution
- EBS dashboard generation and CSV analysis

## Requirements
- Python 3.9+
- AWS CLI v2
- AWS SSO configured

## Installation
git clone git@github.com:Hugo-BerC/SSM-PowerConnect.git
cd SSM-PowerConnect
python3 -m venv .venv
source .venv/bin/activate
pip install -r Windows/requirements.txt

## AWS Configuration
aws configure sso
aws sso login --profile <your-profile>

## Usage
- Windows: python Windows/AWSPWRCNv4.1.py
- macOS: python3 MacOs/AWSPWRCNv4.1.py

## Notes
- The UI background image is `skin.jpg`. The macOS script searches in `MacOs/skin.jpg` and `Windows/skin.jpg`.
