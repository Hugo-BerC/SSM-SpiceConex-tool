from __future__ import annotations
import configparser
import os
from aws.cli import run_aws_cli

def _read(path):
    parser = configparser.ConfigParser()
    if not os.path.isfile(path):
        return path, parser, None
    try:
        parser.read(path)
    except configparser.Error as exc:
        return path, parser, f"{path}: {exc}"
    return path, parser, None

def read_aws_config():
    return _read(os.path.expanduser("~/.aws/config"))

def read_aws_credentials():
    return _read(os.path.expanduser("~/.aws/credentials"))

def profile_name_from_section(section):
    return "default" if section == "default" else section.removeprefix("profile ")

def profiles_from_parser(parser):
    return [profile_name_from_section(section) for section in parser.sections()]

def discover_profiles():
    profiles = set()
    for reader in (read_aws_config, read_aws_credentials):
        _, parser, error = reader()
        if not error:
            profiles.update(profiles_from_parser(parser))
    result = run_aws_cli(["configure", "list-profiles"], check=False)
    if result.returncode == 0:
        profiles.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return sorted(profiles)
