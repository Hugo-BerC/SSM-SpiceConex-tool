# AWSPWRCN

## Overview
AWSPWRCN is a Python tool designed to collect AWS compliance and inventory data across multiple accounts using AWS SSO or role-based access.
It was originally built to run inside WSL (Debian on Windows) but works on any Linux or macOS environment with Python installed.

## Features
- Multi-account inventory collection
- AWS SSM compliance extraction
- CSV output generation
- Modular collector architecture
- Logging system included

## Requirements
- Python 3.9+
- AWS CLI v2
- AWS SSO configured

## Installation
git clone git@github.com:Hugo-BerC/SSM-PowerConnect.git
cd SSM-POWERCONNECT
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

## AWS Configuration
aws configure sso
aws sso login --profile <your-profile>

## Usage
python main.py

## Contributing
1. Fork the repository
2. Create a branch
3. Push changes
4. Open a Pull Request
