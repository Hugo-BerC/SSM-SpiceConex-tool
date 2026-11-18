from __future__ import annotations
import configparser
import os
import subprocess
from pathlib import Path
from aws.cli import run_aws_cli

def _read(path):
    parser = configparser.ConfigParser(interpolation=None)
    if not os.path.isfile(path):
        return path, parser, None
    try:
        with open(path, encoding="utf-8-sig") as handle:
            parser.read_file(handle)
    except (configparser.Error, OSError, UnicodeError) as exc:
        return path, parser, f"{path}: {exc}"
    return path, parser, None

def _aws_paths() -> tuple[str, str]:
    aws_dir = Path.home() / ".aws"
    config = Path(os.environ.get("AWS_CONFIG_FILE", aws_dir / "config")).expanduser()
    credentials = Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE", aws_dir / "credentials")).expanduser()
    return str(config), str(credentials)


def read_aws_config():
    config, _ = _aws_paths()
    return _read(config)

def read_aws_credentials():
    _, credentials = _aws_paths()
    return _read(credentials)

def profile_name_from_section(section):
    return "default" if section == "default" else section.removeprefix("profile ")

def profiles_from_parser(parser, *, credentials=False):
    if credentials:
        # In the shared credentials file section names are literal profile names.
        return [section for section in parser.sections() if section.strip()]
    return [profile_name_from_section(section) for section in parser.sections()
            if section == "default" or (section.startswith("profile ") and section[8:].strip())]

def discover_profiles():
    profiles = set()
    for reader, credentials in ((read_aws_config, False), (read_aws_credentials, True)):
        _, parser, error = reader()
        if not error:
            profiles.update(profiles_from_parser(parser, credentials=credentials))
    try:
        result = run_aws_cli(["configure", "list-profiles"], check=False)
        if result.returncode == 0:
            profiles.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        # Local profiles remain usable when the CLI is missing or unavailable.
        pass
    return sorted(profiles)
