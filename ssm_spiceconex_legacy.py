import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import textwrap
import json
import time
import os
import sys
import shutil
import shlex
import re
import boto3
from PIL import Image, ImageTk, ImageOps
from tkinter import filedialog
import numpy as np
import pandas as pd
import configparser

# Phase 2 architecture: UI remains Tkinter, while extracted layers are available
# to the compatibility UI and future PySide6 controllers.
from app.config import APP_NAME as APP_NAME_CONFIG, DEFAULT_REGION as DEFAULT_REGION_CONFIG, AWS_REGIONS as AWS_REGIONS_CONFIG, AWS_TIMEOUT_SECONDS as AWS_TIMEOUT_SECONDS_CONFIG
from app.state import AppState
from aws.session import get_boto3_session as get_cached_boto3_session, get_ec2_client as get_cached_ec2_client, get_ssm_client as get_cached_ssm_client
from aws.ec2 import list_instances as list_ec2_instances, get_instance_volumes
from aws.profiles import discover_profiles as discover_aws_profiles
from services.instance_service import InstanceService


APP_NAME = APP_NAME_CONFIG
DEFAULT_REGION = DEFAULT_REGION_CONFIG
AWS_REGIONS = AWS_REGIONS_CONFIG
AWS_TIMEOUT_SECONDS = AWS_TIMEOUT_SECONDS_CONFIG

BG_COLOR = "#120d09"
PANEL_COLOR = "#1c130c"
PANEL_ALT_COLOR = "#2a1a0e"
PANEL_DEEP_COLOR = "#130c07"
BORDER_COLOR = "#735738"
TEXT_COLOR = "#f5dfb1"
MUTED_COLOR = "#d6aa72"
ACCENT_COLOR = "#f6c453"
LINK_COLOR = "#f28c28"
ENTRY_BG = "#21150d"
ENTRY_FG = "#f7e4bd"
TREE_BG = "#0f1115"
TREE_ALT_BG = "#151008"
TREE_FG = "#f8e6be"
TREE_SELECTED = "#7c3f16"
OUTPUT_BG = "#0b0d10"
OVERLAY_RGBA = (18, 13, 9, 136)
FONT_NAME = "DejaVu Sans Mono"
FONT = (FONT_NAME, 11)
FONT_SMALL = (FONT_NAME, 10)
FONT_TITLE = (FONT_NAME, 13, "bold")
PORT_WIDTH = 8
CHECK_COLUMN = "Selected"
CHECKED_MARK = "☑"
UNCHECKED_MARK = "☐"
TREE_NAME_INDEX = 1
TREE_INSTANCE_ID_INDEX = 2
COMMAND_DOCUMENTS = {
    "Linux": "AWS-RunShellScript",
    "Windows": "AWS-RunPowerShellScript",
}
checked_instance_ids = set()
app_state = AppState(region=DEFAULT_REGION)



def log_error(message):
    print(f"ERROR: {message}")


def read_aws_config():
    config_path = os.path.expanduser("~/.aws/config")
    parser = configparser.ConfigParser()

    if not os.path.isfile(config_path):
        return config_path, parser, None

    try:
        parser.read(config_path)
    except configparser.Error as exc:
        error_message = f"{config_path}: {exc}"
        log_error(f"Invalid AWS config: {error_message}")
        return config_path, parser, error_message

    return config_path, parser, None


def read_aws_credentials():
    credentials_path = os.path.expanduser("~/.aws/credentials")
    parser = configparser.ConfigParser()

    if not os.path.isfile(credentials_path):
        return credentials_path, parser, None

    try:
        parser.read(credentials_path)
    except configparser.Error as exc:
        error_message = f"{credentials_path}: {exc}"
        log_error(f"Invalid AWS credentials: {error_message}")
        return credentials_path, parser, error_message

    return credentials_path, parser, None


def profile_name_from_section(section):
    if section == "default":
        return "default"
    if section.startswith("profile "):
        return section.replace("profile ", "", 1)
    return section


def profiles_from_parser(parser):
    return [profile_name_from_section(section) for section in parser.sections()]


def get_aws_cli_path():
    aws_path = shutil.which("aws")
    if aws_path:
        return aws_path
    for path in ("/usr/local/bin/aws", "/usr/bin/aws", "/bin/aws"):
        if os.path.isfile(path):
            return path
    return None


def aws_environment():
    env = os.environ.copy()
    home = os.path.expanduser("~")
    env.setdefault("HOME", home)
    env.setdefault("AWS_SDK_LOAD_CONFIG", "1")
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    env.setdefault("AWS_PAGER", "")
    env.setdefault("AWS_CONFIG_FILE", os.path.join(home, ".aws", "config"))
    env.setdefault("AWS_SHARED_CREDENTIALS_FILE", os.path.join(home, ".aws", "credentials"))
    return env


def current_region():
    if "selected_region" in globals():
        try:
            value = selected_region.get().strip()
            if value:
                return value
        except Exception:
            pass
    return DEFAULT_REGION


def current_context():
    profile = ""
    if "selected_profile" in globals():
        try:
            profile = selected_profile.get().strip()
        except Exception:
            profile = ""
    return {"profile": profile, "region": current_region()}


def get_boto3_session(profile=None, region=None):
    """Compatibility wrapper; session lifecycle is owned by aws.session."""
    return get_cached_boto3_session(profile, (region or current_region() or DEFAULT_REGION).strip())


def get_ec2_client(profile=None, region=None):
    return get_cached_ec2_client(profile, (region or current_region() or DEFAULT_REGION).strip())


def get_ssm_client(profile=None, region=None):
    return get_cached_ssm_client(profile, (region or current_region() or DEFAULT_REGION).strip())


def format_aws_cli_error(args, result):
    details = (result.stderr or result.stdout or "").strip()
    if details:
        return details
    return f"aws {shlex.join(args)} exited with status {result.returncode}"


def run_aws_cli(args, check=True):
    aws_path = get_aws_cli_path()
    if not aws_path:
        log_error("AWS CLI not found in PATH.")
        messagebox.showerror("Wake Up!!!", "AWS CLI not found. Install AWS CLI v2 or ensure it is in PATH.")
        raise RuntimeError("AWS CLI not found in PATH.")

    command = [aws_path, *args]
    print(f"Running command: {shlex.join(command)}")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=aws_environment(),
        timeout=AWS_TIMEOUT_SECONDS,
    )

    if check and result.returncode != 0:
        raise RuntimeError(format_aws_cli_error(args, result))

    return result


def run_aws_json(args):
    result = run_aws_cli([*args, "--output", "json"])
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse AWS CLI JSON output: {exc}") from exc


def looks_like_sso_login_error(message):
    lowered = message.lower()
    return (
        "sso" in lowered
        and (
            "token" in lowered
            or "session" in lowered
            or "login" in lowered
            or "unauthorized" in lowered
            or "expired" in lowered
        )
    )


def get_terminal_path():
    for name in ("xterm", "x-terminal-emulator", "kgx", "gnome-terminal", "xfce4-terminal", "konsole"):
        terminal_path = shutil.which(name)
        if terminal_path:
            return terminal_path
    return None


def get_login_shell():
    return shutil.which("bash") or shutil.which("sh") or "/bin/sh"


def build_terminal_command(terminal_path, command):
    shell_path = get_login_shell()
    hold_command = (
        f"{command}; "
        "status=$?; "
        "printf '\\n[SSM-SpiceConex] command exited with status %s. Press Enter to close...' \"$status\"; "
        "read _"
    )
    terminal_name = os.path.basename(terminal_path)

    if terminal_name in ("xterm", "uxterm", "x-terminal-emulator"):
        return [terminal_path, "-T", "SSM-SpiceConex", "-e", shell_path, "-lc", hold_command]
    if terminal_name in ("gnome-terminal", "kgx"):
        return [terminal_path, "--", shell_path, "-lc", hold_command]
    if terminal_name == "xfce4-terminal":
        return [terminal_path, "--hold", "-e", f"{shell_path} -lc {shlex.quote(command)}"]
    if terminal_name == "konsole":
        return [terminal_path, "--hold", "-e", shell_path, "-lc", command]
    return [terminal_path, "-e", shell_path, "-lc", hold_command]


def run_command(command):
    try:
        if command.strip().startswith("aws "):
            aws_path = get_aws_cli_path()
            if not aws_path:
                log_error("AWS CLI not found in PATH.")
                messagebox.showerror("Wake Up!!!", "AWS CLI not found. Install AWS CLI v2 or ensure it is in PATH.")
                return ""
            command = command.replace("aws", shlex.quote(aws_path), 1)
        print(f"Running command: {command}")
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True, env=aws_environment())
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        log_error(f"Error running {command}: {e.stderr}")
        return ""


def terminal_environment():
    env = aws_environment()
    env.setdefault("SSM_SPICECONEX_TERMINAL_TABS", "1")
    env.setdefault("SSM_SPICECONEX_TERMINAL_SESSION", "ssm-spiceconex")
    env.setdefault("SSM_SPICECONEX_TERMINAL_WINDOW_NAME", "SSM-SpiceConex")
    env.setdefault("SHELL", "/bin/bash")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_CTYPE", "en_US.UTF-8")
    env.setdefault("LC_COLLATE", "en_US.UTF-8")
    env.setdefault("TERM", "xterm-256color")
    return env


def sanitize_terminal_title(title):
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".", " ") else "-" for ch in str(title or "SSM-SpiceConex"))
    cleaned = " ".join(cleaned.split())
    return cleaned[:48] or "SSM-SpiceConex"


def get_instance_label(instance_id):
    for inst in instances:
        if inst.get("InstanceID") == instance_id:
            return inst.get("Name") or instance_id
    return instance_id


def selected_instance_pairs():
    pairs = []

    if "checked_instance_ids" in globals() and checked_instance_ids:
        for inst in instances:
            instance_id = str(inst.get("InstanceID", ""))
            if instance_id in checked_instance_ids:
                pairs.append((instance_id, str(inst.get("Name") or instance_id)))
        return pairs

    for item in tree.selection():
        values = tree.item(item).get("values", [])
        if len(values) > TREE_INSTANCE_ID_INDEX:
            instance_id = str(values[TREE_INSTANCE_ID_INDEX])
            pairs.append((instance_id, str(values[TREE_NAME_INDEX] or instance_id)))
    return pairs


def selected_instance_labels():
    if "tree" not in globals():
        return []

    pairs = selected_instance_pairs()
    if pairs:
        return [label for _instance_id, label in pairs]

    labels = []
    for item in tree.selection():
        values = tree.item(item).get("values", [])
        if len(values) > TREE_NAME_INDEX:
            labels.append(str(values[TREE_NAME_INDEX] or values[TREE_INSTANCE_ID_INDEX]))
    return labels


def instance_tree_values(inst):
    instance_id = str(inst.get("InstanceID", ""))
    return (
        CHECKED_MARK if instance_id in checked_instance_ids else UNCHECKED_MARK,
        inst.get("Name") or "No Name",
        instance_id,
        inst.get("PrivateIpAddress", "No IP"),
        inst.get("PlatformDetails", "Unknown"),
        inst.get("InstanceState", "Unknown"),
    )


def insert_instance_row(inst, index):
    return tree.insert(
        "",
        "end",
        values=instance_tree_values(inst),
        tags=("even" if index % 2 == 0 else "odd",),
    )


def refresh_tree_check_marks():
    if "tree" not in globals():
        return
    for item in tree.get_children():
        values = list(tree.item(item).get("values", []))
        if len(values) <= TREE_INSTANCE_ID_INDEX:
            continue
        values[0] = CHECKED_MARK if str(values[TREE_INSTANCE_ID_INDEX]) in checked_instance_ids else UNCHECKED_MARK
        tree.item(item, values=values)


def toggle_instance_checked(item):
    values = tree.item(item).get("values", [])
    if len(values) <= TREE_INSTANCE_ID_INDEX:
        return
    instance_id = str(values[TREE_INSTANCE_ID_INDEX])
    if instance_id in checked_instance_ids:
        checked_instance_ids.remove(instance_id)
    else:
        checked_instance_ids.add(instance_id)
    refresh_tree_check_marks()
    update_selected_instance_context()


def toggle_visible_instances_checked():
    visible_ids = []
    for item in tree.get_children():
        values = tree.item(item).get("values", [])
        if len(values) > TREE_INSTANCE_ID_INDEX:
            visible_ids.append(str(values[TREE_INSTANCE_ID_INDEX]))

    if not visible_ids:
        return

    if all(instance_id in checked_instance_ids for instance_id in visible_ids):
        for instance_id in visible_ids:
            checked_instance_ids.discard(instance_id)
    else:
        checked_instance_ids.update(visible_ids)

    refresh_tree_check_marks()
    update_selected_instance_context()


def on_instance_tree_click(event):
    if tree.identify_region(event.x, event.y) != "cell":
        return None
    if tree.identify_column(event.x) != "#1":
        return None
    item = tree.identify_row(event.y)
    if not item:
        return None
    toggle_instance_checked(item)
    return "break"


def update_instance_count(visible_count=None):
    if "instance_count_var" not in globals():
        return
    total_count = len(instances) if "instances" in globals() else 0
    if visible_count is None:
        visible_count = total_count
    if total_count and visible_count != total_count:
        instance_count_var.set(f"{visible_count} visible / {total_count} total")
    elif total_count == 1:
        instance_count_var.set("1 instance")
    else:
        instance_count_var.set(f"{total_count} instances")


def update_selected_instance_context(event=None):
    labels = selected_instance_labels()
    if not labels:
        text = "No instance selected"
    elif len(labels) == 1:
        text = labels[0]
    else:
        text = f"{len(labels)} instances selected"

    if "tunnel_target_var" in globals():
        tunnel_target_var.set(text)
    if "command_target_var" in globals():
        command_target_var.set(text)
    if "connectivity_source_var" in globals():
        connectivity_source_var.set(text)


def clear_command_output():
    if "command_output" not in globals():
        return
    command_output.configure(state="normal")
    command_output.delete("1.0", tk.END)
    command_output.configure(state="disabled")


def open_terminal_command(command, title=None):
    if not os.environ.get("DISPLAY"):
        messagebox.showerror(
            "Graphics unavailable",
            "No graphical display was detected. Launch SSM-SpiceConex from a desktop session.",
        )
        return False

    terminal_path = get_terminal_path()
    if not terminal_path:
        messagebox.showerror("Wake Up!!!", "No graphical terminal found. Install xterm or another supported terminal emulator.")
        return False

    env = terminal_environment()
    if title:
        env["SSM_SPICECONEX_TERMINAL_WINDOW_NAME"] = sanitize_terminal_title(title)

    subprocess.Popen(build_terminal_command(terminal_path, command), env=env, start_new_session=True)
    return True


def resize_instance_tree(event=None):
    if "tree" not in globals():
        return

    width = tree.winfo_width()
    if event is not None and getattr(event, "width", 0):
        width = event.width

    tree.column(CHECK_COLUMN, width=64, minwidth=56, anchor="center", stretch=False)

    available = max(width - 92, 760)
    columns = (
        ("Name", 0.34, 220, "w"),
        ("InstanceID", 0.22, 170, "w"),
        ("PrivateIP", 0.15, 120, "w"),
        ("PlatformDetails", 0.17, 150, "w"),
        ("InstanceState", 0.12, 100, "center"),
    )

    for column_name, ratio, min_width, anchor in columns:
        tree.column(
            column_name,
            width=max(int(available * ratio), min_width),
            minwidth=min_width,
            anchor=anchor,
            stretch=True,
        )


def get_skin_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    bundle_dir = getattr(sys, "_MEIPASS", None)
    candidates = [
        os.path.join(script_dir, "skin.jpg"),
        os.path.join(script_dir, "assets", "skin.jpg"),
        os.path.join(script_dir, "..", "MacOs", "skin.jpg"),
        os.path.join(script_dir, "..", "Windows", "skin.jpg"),
    ]
    if bundle_dir:
        candidates.insert(0, os.path.join(bundle_dir, "skin.jpg"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def get_profiles():
    return discover_aws_profiles()


def validate_session(profile):
    try:
        identity = get_caller_identity(profile, current_region())
        return bool(identity.get("Account"))
    except Exception as exc:
        log_error(f"Session validation failed for {profile}: {exc}")
        return False


def validate_session_async():
    profile = selected_profile.get().strip() if "selected_profile" in globals() else ""
    if not profile:
        messagebox.showwarning("AWS profile", "Select an AWS profile first.")
        return
    def worker():
        valid = validate_session(profile)
        if valid:
            app_root.after(0, lambda: messagebox.showinfo("AWS validation", "AWS STS validation succeeded for the selected profile."))
        else:
            app_root.after(0, lambda: messagebox.showerror("AWS validation", "AWS STS validation failed for the selected profile."))
    threading.Thread(target=worker, daemon=True).start()


def login_sso(profile):
    aws_path = get_aws_cli_path() or "aws"
    open_terminal_command(
        "AWS_SDK_LOAD_CONFIG=1 AWS_EC2_METADATA_DISABLED=true AWS_PAGER= "
        f"{shlex.quote(aws_path)} sso login --profile {shlex.quote(profile)}"
    )


def get_instances(profile):
    try:
        items = list_ec2_instances(profile, current_region())
        return [item.as_legacy_dict() for item in items]
    except Exception as exc:
        log_error(f"Error getting instances from profile {profile}: {exc}")
        raise RuntimeError(f"Cannot get instances from profile {profile}: {exc}") from exc


def filter_profiles(event):
    search_text = profile_search_entry.get().lower()
    filtered_profiles = []
    for profile in profiles:
        if search_text in profile.lower():
            filtered_profiles.append(profile)
            continue
    profile_menu['values'] = filtered_profiles

    if filtered_profiles:
        profile_menu.current(0)
    else:
        profile_menu.set('')


def filter_instances(event):
    search_text = instance_search_entry.get().lower()
    filtered_instances = [
        inst for inst in instances
        if search_text in str(inst['Name']).lower()
        or search_text in str(inst['InstanceID']).lower()
        or search_text in str(inst.get('PlatformDetails', '')).lower()
    ]

    for row in tree.get_children():
        tree.delete(row)

    for index, inst in enumerate(filtered_instances):
        insert_instance_row(inst, index)

    update_instance_count(len(filtered_instances))
    update_selected_instance_context()


def refresh_instances():
    global instances
    profile = selected_profile.get()
    if not profile:
        messagebox.showerror("Wake Up!!!", "Select valid profile.")
        return

    instances = []  # Limpiar siempre antes de cargar

    try:
        instances = get_instances(profile)
        app_state.profile = profile
        app_state.region = current_region()
        app_state.set_instances([
            __import__("models.instance", fromlist=["EC2Instance"]).EC2Instance(
                inst["InstanceID"], inst["Name"],
                None if inst.get("PrivateIpAddress") == "No IP" else inst.get("PrivateIpAddress"),
                inst.get("PlatformDetails", "Unknown"), inst.get("InstanceState", "unknown")
            ) for inst in instances
        ])
        checked_instance_ids.clear()
        app_state.clear_selection()
    except Exception as e:
        error_message = str(e).strip() or repr(e)
        print(error_message)
        if looks_like_sso_login_error(error_message):
            messagebox.showerror(
                "AWS SSO",
                f"SSO session is not active for profile:\n{profile}\n\n{error_message}\n\n"
                "Complete aws sso login and press Refresh again.",
            )
            login_sso(profile)
        else:
            messagebox.showerror(
                "AWS refresh failed",
                f"Could not load EC2 instances for profile:\n{profile}\n\n"
                f"Region: {current_region()}\n\n{error_message}",
            )
        return

    if not instances:
        messagebox.showerror("Wake Up!!!", f"Instances not found on: {profile}")
        return

    for row in tree.get_children():
        tree.delete(row)

    for index, inst in enumerate(instances):
        insert_instance_row(inst, index)

    update_instance_count(len(instances))
    update_selected_instance_context()


def connect_selected_instances():
    profile = selected_profile.get()
    instance_pairs = selected_instance_pairs()
    if not instance_pairs:
        typed_id = instance_search_entry.get().strip()
        if typed_id.startswith("i-"):
            instance_pairs = [(typed_id, get_instance_label(typed_id))]
        else:
            messagebox.showerror("Wake Up!!!", "Select an instance or type a valid Instance ID (i-...).")
            return
    threading.Thread(target=connect_to_instance, args=(profile, instance_pairs), daemon=True).start()


def connect_to_instance(profile, instance_pairs):
    """Inicia una sesion SSM en la instancia seleccionada."""
    try:
        if not instance_pairs:
            messagebox.showerror("Wake Up!!!", "No instances selected.")
            return

        for instance_id, instance_name in instance_pairs:
            aws_path = get_aws_cli_path() or "aws"
            command = (
                f"{shlex.quote(aws_path)} ssm start-session "
                f"--target {shlex.quote(instance_id)} --profile {shlex.quote(profile)} "
                f"--region {shlex.quote(current_region())}"
            )
            open_terminal_command(command, title=instance_name)

    except Exception as e:
        log_error(f"Error al conectar a la instancia: {e}")
        messagebox.showerror("Wake Up!!!", f"Cannot connect to instance:\n{e}")


def open_tunnel_with_terminal():
    """Abre un tunel en la instancia seleccionada con parametros especificos."""
    try:
        instance_pairs = selected_instance_pairs()
        if instance_pairs:
            instance_id, instance_name = instance_pairs[0]
        else:
            instance_id = instance_search_entry.get().strip()
            if not instance_id.startswith("i-"):
                messagebox.showerror("Wake Up!!!", "Select an instance or type a valid Instance ID (i-...).")
                return
            instance_name = get_instance_label(instance_id)
        profile = selected_profile.get()

        if not profile:
            messagebox.showerror("Wake Up!!!", "Select profile")
            return

        localport = localport_entry.get()
        remoteport = remoteport_entry.get()

        if not localport or not remoteport:
            messagebox.showerror("Wake Up!!!", " Type LocalPort and RemotePort.")
            return
        for label, port in (("LocalPort", localport), ("RemotePort", remoteport)):
            if not port.isdigit():
                messagebox.showerror("Wake Up!!!", f"{label} must be numeric.")
                return
            if not 0 <= int(port) <= 65535:
                messagebox.showerror("Wake Up!!!", f"{label} must be between 0 and 65535.")
                return

        aws_path = get_aws_cli_path() or "aws"
        command = (
            f"{shlex.quote(aws_path)} ssm start-session --target "
            f"{shlex.quote(instance_id)} --profile {shlex.quote(profile)} "
            f"--region {shlex.quote(current_region())} "
            "--document-name AWS-StartPortForwardingSession "
            f"--parameters portNumber={shlex.quote(remoteport)},localPortNumber={shlex.quote(localport)}"
        )

        open_terminal_command(command, title=f"{instance_name}-tunnel")

    except Exception as e:
        log_error(f"Error al abrir tunel: {e}")
        messagebox.showerror("Wake Up!!!", f"No se pudo abrir el tunel:\n{e}")


def invocation():
    try:
        instance_pairs = selected_instance_pairs()
        if not instance_pairs:
            raise ValueError("Debe seleccionar al menos una instancia.")

        instance_ids = [instance_id for instance_id, _label in instance_pairs]
        command = command_input.get("1.0", tk.END).strip()
        if not command:
            raise ValueError("Debe ingresar un comando para ejecutar.")

        os_family = command_os_var.get() or "Linux"
        document_name = COMMAND_DOCUMENTS.get(os_family, COMMAND_DOCUMENTS["Linux"])

        threading.Thread(
            target=sendcommand,
            args=(instance_ids, command, selected_profile.get(), document_name, os_family),
            daemon=True
        ).start()
        print(instance_ids)
    except Exception as e:
        messagebox.showerror("Wake Up!!!", str(e))


def sendcommand(instance_ids, command, profile, document_name, os_family):
    print(instance_ids)
    output = []
    for instance in instance_ids:
        try:
            print(f"Sending {os_family} command on instance: {instance}")
            ssm_client = get_ssm_client(profile=profile)

            response = ssm_client.send_command(
                InstanceIds=[instance],
                DocumentName=document_name,
                Parameters={'commands': [command]}
            )

            command_id = response['Command']['CommandId']
            time.sleep(1)

            invocation_result = ssm_client.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance
            )
            while invocation_result['Status'] in ("Pending", "InProgress", "Delayed"):
                time.sleep(1)
                invocation_result = ssm_client.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance
                )
            standard_output = invocation_result['StandardOutputContent']
            standard_error = invocation_result.get('StandardErrorContent', '')
            print(standard_output)

            instance_output = f"Instance {instance} ({os_family}, {document_name}):\n{standard_output}"
            if standard_error:
                instance_output += f"\nSTDERR:\n{standard_error}"
            output.append(f"{instance_output}\n{'='*120}\n")

        except Exception as e:
            print(e)
            messagebox.showerror("Wake Up!!!", str(e))

    command_output.configure(state="normal")
    command_output.delete("1.0", tk.END)
    command_output.insert(tk.END, "\n".join(output))
    command_output.configure(state="disabled")


def normalize_connectivity_header(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def normalize_connectivity_value(value):
    text = "" if value is None else str(value).strip()
    text = text.replace("|", "/").replace("\n", " ").replace("\r", " ")
    return text


def normalize_connectivity_scope(value):
    normalized = normalize_connectivity_header(value)
    if normalized in {"internal", "interno", "private", "privado", "local"}:
        return "internal"
    if normalized in {"external", "externo", "public", "publico"}:
        return "external"
    return normalize_connectivity_value(value) or "unknown"


def parse_connectivity_csv(csv_path):
    if not csv_path:
        raise ValueError("Select a CSV file first.")

    dataframe = pd.read_csv(csv_path)
    if dataframe.empty:
        raise ValueError("The CSV file is empty.")

    columns = {normalize_connectivity_header(column): column for column in dataframe.columns}

    def pick_column(required, *aliases):
        for alias in aliases:
            normalized = normalize_connectivity_header(alias)
            if normalized in columns:
                return columns[normalized]
        if required:
            raise ValueError(f"Missing required CSV column. Accepted names: {', '.join(aliases)}")
        return None

    service_column = pick_column(True, "service_name", "service", "nombre del servicio", "nombre_servicio", "servicio", "name")
    destination_column = pick_column(True, "destination", "destino", "ip destino", "ip_destino", "host", "target")
    port_column = pick_column(True, "port", "puerto")
    scope_column = pick_column(True, "scope", "type", "tipo", "internal/external", "interno o externo", "interno_externo")
    protocol_column = pick_column(False, "protocol", "protocolo")

    rows = []
    for index, row in dataframe.iterrows():
        service = normalize_connectivity_value(row[service_column])
        destination = normalize_connectivity_value(row[destination_column])
        port = normalize_connectivity_value(row[port_column])
        scope = normalize_connectivity_scope(row[scope_column])
        protocol = normalize_connectivity_value(row[protocol_column]).upper() if protocol_column else "TCP"

        if not service or not destination or not port or not scope:
            raise ValueError(f"Row {index + 2} is missing a required value.")

        if not str(port).isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError(f"Row {index + 2} has an invalid port: {port}")

        rows.append(
            {
                "service": service,
                "destination": destination,
                "port": str(port),
                "scope": scope,
                "protocol": protocol or "TCP",
            }
        )

    if not rows:
        raise ValueError("No valid connectivity rows were found in the CSV.")

    return rows


def build_connectivity_script(rows, timeout_seconds):
    sanitized_rows = [
        f"{normalize_connectivity_value(row['service'])}|{normalize_connectivity_value(row['destination'])}|{normalize_connectivity_value(row['port'])}|{normalize_connectivity_value(row['scope'])}"
        for row in rows
    ]
    heredoc_rows = "\n".join(sanitized_rows)

    timeout_seconds = max(1, int(timeout_seconds))
    nc_timeout = max(1, min(timeout_seconds, 10))
    command_timeout = max(timeout_seconds + 2, nc_timeout + 1)

    return textwrap.dedent(
        f"""        set +e
        CONNECTION_TIMEOUT={command_timeout}
        NC_TIMEOUT={nc_timeout}
        NC_BIN="$(command -v nc || command -v ncat || true)"

        if [ -z "$NC_BIN" ]; then
            printf 'ERROR|nc not found|0|unknown|KO|0|nc command not found\n'
            exit 0
        fi

        while IFS='|' read -r service destination port scope; do
            [ -z "$service" ] && continue

            start_ms=$(date +%s%3N 2>/dev/null)
            case "$start_ms" in
                ""|*[!0-9]*) start_ms=$(( $(date +%s) * 1000 ));;
            esac

            output=""
            code=0
            if command -v timeout >/dev/null 2>&1; then
                output=$(timeout "${{CONNECTION_TIMEOUT}}s" "$NC_BIN" -vz -w "$NC_TIMEOUT" "$destination" "$port" 2>&1)
                code=$?
            else
                output=$("$NC_BIN" -vz -w "$NC_TIMEOUT" "$destination" "$port" 2>&1)
                code=$?
            fi

            end_ms=$(date +%s%3N 2>/dev/null)
            case "$end_ms" in
                ""|*[!0-9]*) end_ms=$(( $(date +%s) * 1000 ));;
            esac
            duration=$(( end_ms - start_ms ))

            normalized_output=$(printf '%s' "$output" | tr '\r\n' ' ' | tr '|' '/')
            status="KO"
            detail="connection failed"

            if [ "$code" -eq 0 ]; then
                status="OK"
                detail="connected"
            elif [ "$code" -eq 124 ]; then
                status="TIMEOUT"
                detail="command timeout"
            else
                case "$normalized_output" in
                    *timed*out*|*Timed*out*)
                        status="TIMEOUT"
                        ;;
                    *refused*|*Refused*)
                        status="REFUSED"
                        ;;
                    *unreachable*|*Unreachable*|*No*route*)
                        status="UNREACHABLE"
                        ;;
                esac
                [ -n "$normalized_output" ] && detail="$normalized_output"
            fi

            printf '%s|%s|%s|%s|%s|%s|%s\n' "$service" "$destination" "$port" "$scope" "$status" "$duration" "$detail"
        done <<'EOF_CONNECTIVITY'
        {heredoc_rows}
        EOF_CONNECTIVITY
        """
    ).strip()


def parse_connectivity_output(stdout):
    results = []
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("|", 6)
        if len(parts) < 6:
            continue
        while len(parts) < 7:
            parts.append("")
        service, destination, port, scope, status, duration, detail = parts[:7]
        results.append(
            {
                "service": service,
                "destination": destination,
                "port": port,
                "scope": scope,
                "status": status or "KO",
                "duration": duration,
                "detail": detail,
            }
        )
    return results


def connectivity_status_color(status):
    normalized = str(status).strip().upper()
    if normalized == "OK":
        return "#4caf50"
    if normalized in {"TIMEOUT", "REFUSED", "UNREACHABLE"}:
        return "#e53935"
    return "#fb8c00"


def update_connectivity_detail(result):
    if "connectivity_detail_text" not in globals():
        return
    connectivity_detail_text.configure(state="normal")
    connectivity_detail_text.delete("1.0", tk.END)
    connectivity_detail_text.insert(
        tk.END,
        (
            f"Service: {result.get('service', '')}\n"
            f"Destination: {result.get('destination', '')}\n"
            f"Port: {result.get('port', '')}\n"
            f"Scope: {result.get('scope', '')}\n"
            f"Status: {result.get('status', '')}\n"
            f"Duration: {result.get('duration', '')} ms\n"
            f"Detail: {result.get('detail', '')}\n"
        ),
    )
    connectivity_detail_text.configure(state="disabled")


def draw_connectivity_map(results, source_label):
    if "connectivity_canvas" not in globals():
        return

    canvas = connectivity_canvas
    canvas.delete("all")

    try:
        width = max(900, int(canvas.winfo_width()))
    except Exception:
        width = 900

    source_x = width // 2
    source_y = 52
    source_box = (source_x - 120, source_y - 22, source_x + 120, source_y + 22)

    canvas.create_text(
        source_x,
        16,
        text="Connectivity map",
        fill=ACCENT_COLOR,
        font=FONT_TITLE,
        tags=("connectivity_static",),
    )
    canvas.create_text(
        source_x,
        source_y - 40,
        text=source_label or "Selected instance",
        fill=TEXT_COLOR,
        font=FONT_TITLE,
        tags=("connectivity_static",),
    )
    canvas.create_rectangle(*source_box, outline=ACCENT_COLOR, width=2, fill=PANEL_ALT_COLOR, tags=("connectivity_static",))
    canvas.create_text(
        source_x,
        source_y,
        text="SOURCE",
        fill=ACCENT_COLOR,
        font=FONT_TITLE,
        tags=("connectivity_static",),
    )

    if not results:
        canvas.create_text(
            source_x,
            140,
            text="Load a CSV and run the validation to render the network.",
            fill=MUTED_COLOR,
            font=FONT_SMALL,
            tags=("connectivity_static",),
        )
        canvas.configure(scrollregion=canvas.bbox("all"))
        return

    internal_results = [result for result in results if normalize_connectivity_scope(result.get("scope")) == "internal"]
    external_results = [result for result in results if normalize_connectivity_scope(result.get("scope")) == "external"]
    other_results = [result for result in results if result not in internal_results and result not in external_results]

    groups = [
        ("Internal", internal_results, 160),
        ("External", external_results, 290),
        ("Other", other_results, 420),
    ]

    node_meta = {}
    node_index = 0

    for group_name, group_results, base_y in groups:
        if not group_results:
            continue

        canvas.create_text(
            100,
            base_y - 32,
            text=group_name,
            anchor="w",
            fill=ACCENT_COLOR,
            font=FONT_TITLE,
            tags=("connectivity_static",),
        )

        total = len(group_results)
        x_padding = 90
        usable_width = max(420, width - (x_padding * 2))
        step = usable_width if total == 1 else usable_width / max(total - 1, 1)
        x_positions = [x_padding + (step * idx) for idx in range(total)]
        if total == 1:
            x_positions = [width // 2]

        for result, x in zip(group_results, x_positions):
            status_color = connectivity_status_color(result.get("status"))
            box_width = 170
            box_height = 52
            top_left = (x - box_width / 2, base_y - box_height / 2)
            bottom_right = (x + box_width / 2, base_y + box_height / 2)

            canvas.create_line(source_x, source_y + 22, x, base_y - box_height / 2, fill=status_color, width=2, tags=("connectivity_static",))
            rect_id = canvas.create_rectangle(*top_left, *bottom_right, outline=status_color, width=2, fill=PANEL_COLOR, tags=(f"node_{node_index}", "connectivity_node"))
            title_id = canvas.create_text(
                x,
                base_y - 12,
                text=result.get("service", ""),
                fill=TEXT_COLOR,
                font=FONT_TITLE,
                tags=(f"node_{node_index}", "connectivity_node"),
            )
            subtitle_id = canvas.create_text(
                x,
                base_y + 6,
                text=f"{result.get('destination', '')}:{result.get('port', '')}",
                fill=MUTED_COLOR,
                font=FONT_SMALL,
                tags=(f"node_{node_index}", "connectivity_node"),
            )
            status_id = canvas.create_text(
                x,
                base_y + 22,
                text=f"{result.get('status', '')} · {result.get('scope', '')}",
                fill=status_color,
                font=FONT_SMALL,
                tags=(f"node_{node_index}", "connectivity_node"),
            )

            node_meta[f"node_{node_index}"] = result
            for item_id in (rect_id, title_id, subtitle_id, status_id):
                canvas.tag_bind(item_id, "<Button-1>", lambda _event, key=f"node_{node_index}": update_connectivity_detail(node_meta[key]))
            node_index += 1

    canvas.configure(scrollregion=canvas.bbox("all"))


def clear_connectivity_results():
    if "connectivity_results_tree" in globals():
        for row in connectivity_results_tree.get_children():
            connectivity_results_tree.delete(row)
    if "connectivity_canvas" in globals():
        connectivity_canvas.delete("all")
    if "connectivity_detail_text" in globals():
        connectivity_detail_text.configure(state="normal")
        connectivity_detail_text.delete("1.0", tk.END)
        connectivity_detail_text.configure(state="disabled")


def render_connectivity_results(results, source_label, profile, region):
    if "connectivity_summary_var" in globals():
        ok_count = sum(1 for result in results if str(result.get("status", "")).upper() == "OK")
        ko_count = len(results) - ok_count
        connectivity_summary_var.set(f"Validated {len(results)} services · OK {ok_count} · KO {ko_count} · Profile {profile} · Region {region}")

    if "connectivity_results_tree" in globals():
        for row in connectivity_results_tree.get_children():
            connectivity_results_tree.delete(row)
        for result in results:
            tag = "ok" if str(result.get("status", "")).upper() == "OK" else "ko"
            connectivity_results_tree.insert(
                "",
                "end",
                values=(
                    result.get("service", ""),
                    result.get("destination", ""),
                    result.get("port", ""),
                    result.get("scope", ""),
                    result.get("status", ""),
                    result.get("duration", ""),
                    result.get("detail", ""),
                ),
                tags=(tag,),
            )
        connectivity_results_tree.tag_configure("ok", background="#12351a", foreground="#dcfce7")
        connectivity_results_tree.tag_configure("ko", background="#381619", foreground="#ffd7d7")
        items = connectivity_results_tree.get_children()
        if items:
            connectivity_results_tree.selection_set(items[0])
            connectivity_results_tree.focus(items[0])

    draw_connectivity_map(results, source_label)
    if results:
        update_connectivity_detail(results[0])


def load_connectivity_csv():
    try:
        csv_path = filedialog.askopenfilename(
            title="Select connectivity CSV",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if not csv_path:
            return

        rows = parse_connectivity_csv(csv_path)
        globals()["connectivity_rows"] = rows
        globals()["connectivity_csv_path"] = csv_path
        if "connectivity_csv_var" in globals():
            connectivity_csv_var.set(f"{os.path.basename(csv_path)} · {len(rows)} services loaded")
        if "connectivity_summary_var" in globals():
            connectivity_summary_var.set(f"Loaded {len(rows)} services from {os.path.basename(csv_path)}")
        if "connectivity_input_tree" in globals():
            for row in connectivity_input_tree.get_children():
                connectivity_input_tree.delete(row)
            for row in rows:
                connectivity_input_tree.insert(
                    "",
                    "end",
                    values=(
                        row.get("service", ""),
                        row.get("destination", ""),
                        row.get("port", ""),
                        row.get("scope", ""),
                        row.get("protocol", ""),
                    ),
                )
        messagebox.showinfo("Connectivity CSV", f"Loaded {len(rows)} services from:\n{csv_path}")
    except Exception as exc:
        log_error(f"Connectivity CSV error: {exc}")
        messagebox.showerror("Connectivity CSV", str(exc))


def execute_connectivity_validation(profile, region, instance_id, instance_label, rows, timeout_seconds):
    try:
        script = build_connectivity_script(rows, timeout_seconds)
        ssm_client = get_ssm_client(profile=profile, region=region)
        response = ssm_client.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [script]},
        )
        command_id = response["Command"]["CommandId"]

        invocation_result = ssm_client.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        while invocation_result["Status"] in ("Pending", "InProgress", "Delayed"):
            time.sleep(1)
            invocation_result = ssm_client.get_command_invocation(CommandId=command_id, InstanceId=instance_id)

        stdout = invocation_result.get("StandardOutputContent", "")
        stderr = invocation_result.get("StandardErrorContent", "")
        results = parse_connectivity_output(stdout)

        if not results and stderr:
            raise RuntimeError(stderr.strip() or "Connectivity validation returned no output.")

        def finalize():
            globals()["connectivity_results"] = results
            render_connectivity_results(results, instance_label, profile, region)
            if stderr.strip() and "connectivity_summary_var" in globals():
                connectivity_summary_var.set(stderr.strip())
            if not results and "connectivity_summary_var" in globals():
                connectivity_summary_var.set(f"No parsable results returned for {instance_label} in {region}")

        if "app_root" in globals():
            app_root.after(0, finalize)
        else:
            finalize()

    except Exception as exc:
        def show_error():
            if "connectivity_summary_var" in globals():
                connectivity_summary_var.set(f"Connectivity validation failed for {instance_label}: {exc}")
            messagebox.showerror("Connectivity validation", str(exc))

        if "app_root" in globals():
            app_root.after(0, show_error)
        else:
            show_error()


def run_connectivity_validation():
    try:
        profile = selected_profile.get().strip()
        region = current_region()
        timeout_value = connectivity_timeout_var.get().strip()
        rows = globals().get("connectivity_rows", [])

        if not profile:
            raise ValueError("Select a profile first.")
        if not rows:
            raise ValueError("Load a connectivity CSV first.")
        if not timeout_value.isdigit() or int(timeout_value) < 1:
            raise ValueError("Timeout must be a positive number.")
        instance_pairs = selected_instance_pairs()
        if not instance_pairs:
            raise ValueError("Select one source instance in Terminal first.")

        instance_id, instance_label = instance_pairs[0]
        timeout_seconds = int(timeout_value)

        if "connectivity_summary_var" in globals():
            connectivity_summary_var.set(f"Running validation from {instance_label} in {region}...")
        if "connectivity_detail_text" in globals():
            connectivity_detail_text.configure(state="normal")
            connectivity_detail_text.delete("1.0", tk.END)
            connectivity_detail_text.insert(tk.END, "Running connectivity validation...\n")
            connectivity_detail_text.configure(state="disabled")

        threading.Thread(
            target=execute_connectivity_validation,
            args=(profile, region, instance_id, instance_label, rows, timeout_seconds),
            daemon=True,
        ).start()

    except Exception as exc:
        messagebox.showerror("Connectivity validation", str(exc))

def add_tab_with_background(notebook, text, image_path):
    frame = ttk.Frame(notebook, style="TFrame")
    notebook.add(frame, text=text)

    canvas = tk.Canvas(frame, highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    bg_image = Image.open(image_path)
    bg_photo = ImageTk.PhotoImage(bg_image)
    canvas.bg_photo = bg_photo

    bg_id = canvas.create_image(0, 0, anchor="nw", image=bg_photo)

    def resize_background(event):
        canvas.coords(bg_id, 0, 0)
        canvas.itemconfig(bg_id, image=canvas.bg_photo)

    canvas.bind("<Configure>", resize_background)

    return frame, canvas


def create_tab_with_background(notebook, text, image_path):
    frame = tk.Frame(notebook, bg=BG_COLOR)
    notebook.add(frame, text=text)

    if image_path and os.path.isfile(image_path):
        background_label = tk.Label(frame, borderwidth=0, highlightthickness=0)
        background_label.place(x=0, y=0, relwidth=1, relheight=1)
        frame._skin_original = Image.open(image_path).convert("RGB")
        frame._skin_photo = None

        def resize_skin(event):
            if event.width < 2 or event.height < 2:
                return
            resample_filter = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            canvas_image = ImageOps.fit(
                frame._skin_original,
                (event.width, event.height),
                method=resample_filter,
                centering=(0.5, 0.5),
            )
            overlay = Image.new("RGBA", canvas_image.size, OVERLAY_RGBA)
            canvas_image = Image.alpha_composite(canvas_image.convert("RGBA"), overlay)
            frame._skin_photo = ImageTk.PhotoImage(canvas_image)
            background_label.configure(image=frame._skin_photo)
            background_label.lower()

        frame.bind("<Configure>", resize_skin)

    return frame


def calcular_lineas_base(archivo_csv):
    try:
        df = pd.read_csv(archivo_csv)

        if df.shape[1] < 3:
            raise ValueError("El archivo debe tener al menos tres columnas: Timestamp, IOPS por minuto y Bytes por segundo.")

        df['timestamp'] = pd.to_datetime(df.iloc[:, 0])

        iops_col = df.iloc[:, 1]
        bytes_seg_col = df.iloc[:, 2]

        iops_seg = iops_col
        throughput_mb = bytes_seg_col / 1_048_576

        throughput_min = throughput_mb.min()
        throughput_max = throughput_mb.max()
        throughput_prom = throughput_mb.mean()
        throughput_std = throughput_mb.std()

        iops_min = iops_seg.min()
        iops_max = iops_seg.max()
        iops_prom = iops_seg.mean()
        iops_std = iops_seg.std()

        throughput_percentil_95 = np.percentile(throughput_mb, 95)
        iops_percentil_95 = np.percentile(iops_seg, 95)

        df_filtered = df[(throughput_mb <= throughput_percentil_95) & (iops_seg <= iops_percentil_95)]

        recommended_throughput_95 = np.percentile(throughput_mb, 95)
        recommended_iops_95 = np.percentile(iops_seg, 95)

        recommended_throughput_99 = np.percentile(throughput_mb, 99)
        recommended_iops_99 = np.percentile(iops_seg, 99)

        recommended_throughput_max = throughput_mb.max() * 1.1
        recommended_iops_max = iops_seg.max() * 1.1

        microbursts_throughput = []
        microbursts_iops = []

        in_microburst = False
        microburst_start = None
        for idx, value in enumerate(throughput_mb):
            if value > recommended_throughput_99:
                if not in_microburst:
                    microburst_start = df['timestamp'][idx]
                    in_microburst = True
            else:
                if in_microburst:
                    microburst_end = df['timestamp'][idx]
                    duration = (microburst_end - microburst_start).total_seconds()
                    microbursts_throughput.append((microburst_start, microburst_end, duration))
                    in_microburst = False

        in_microburst = False
        for idx, value in enumerate(iops_seg):
            if value > recommended_iops_99:
                if not in_microburst:
                    microburst_start = df['timestamp'][idx]
                    in_microburst = True
            else:
                if in_microburst:
                    microburst_end = df['timestamp'][idx]
                    duration = (microburst_end - microburst_start).total_seconds()
                    microbursts_iops.append((microburst_start, microburst_end, duration))
                    in_microburst = False

        resultados = {
            'Linea Base Minima de Throughput (MB/s)': throughput_min,
            'Linea Base Maxima de Throughput (MB/s)': throughput_max,
            'Linea Base Promedio de Throughput (MB/s)': throughput_prom,
            'Linea Base Minima de IOPS/s': iops_min,
            'Linea Base Maxima de IOPS/s': iops_max,
            'Linea Base Promedio de IOPS/s': iops_prom,
            'Recomendacion de Throughput para EBS (MB/s)': recommended_throughput_95,
            'Recomendacion de IOPS para EBS': recommended_iops_95,
            'Recomendacion de Throughput para EBS (Microbursting) (MB/s)': recommended_throughput_99,
            'Recomendacion de IOPS para EBS (Microbursting)': recommended_iops_99,
            'Recomendacion de Throughput para EBS (Techo Maximo) (MB/s)': recommended_throughput_max,
            'Recomendacion de IOPS para EBS (Techo Maximo)': recommended_iops_max,
            'Microbursts de Throughput (Momento de Inicio, Momento de Fin, Duracion en Segundos)': microbursts_throughput,
            'Microbursts de IOPS (Momento de Inicio, Momento de Fin, Duracion en Segundos)': microbursts_iops
        }

        return resultados

    except Exception as e:
        messagebox.showerror("Error", f"Hubo un problema al procesar el archivo: {str(e)}")


def seleccionar_archivo():
    archivo = filedialog.askopenfilename(
        title="Seleccionar archivo CSV",
        filetypes=(("Archivos CSV", "*.csv"), ("Todos los archivos", "*.*"))
    )
    if archivo:
        resultados = calcular_lineas_base(archivo)

        if resultados:
            analysis_output.configure(state="normal")
            analysis_output.delete(1.0, tk.END)

            for key, value in resultados.items():
                if isinstance(value, dict):
                    analysis_output.insert(tk.END, f"{key}:\n")
                    for sub_key, sub_value in value.items():
                        analysis_output.insert(tk.END, f"  {sub_key}: {sub_value:.2f}\n")
                elif isinstance(value, list):
                    analysis_output.insert(tk.END, f"{key}:\n")
                    for item in value:
                        analysis_output.insert(tk.END, f"  {item}\n")
                else:
                    if isinstance(value, float):
                        analysis_output.insert(tk.END, f"{key}: {value:.2f}\n")
                    else:
                        analysis_output.insert(tk.END, f"{key}: {value}\n")
            analysis_output.insert(tk.END, "\n")


def ebs_analysis():
    try:
        instance_pairs = selected_instance_pairs()
        if not instance_pairs:
            messagebox.showerror("Wake Up!!!", "No instances selected")
            return

        instance_id, name = instance_pairs[0]
        formatted_name = "EBS_Analysis_" + name.split('.')[0]
        profile = selected_profile.get()

        if not profile:
            messagebox.showerror("Wake Up!!!", "Select profile")
            return

    except Exception as e:
        log_error(f"Cannot create dashboard: {e}")
        messagebox.showerror("Wake Up!!!", f"Cannot create dashboard:\n{e}")

    region = current_region()
    session = get_boto3_session(profile=profile, region=region)
    ec2_client = session.client('ec2')
    cloudwatch_client = session.client('cloudwatch')

    volumes = ec2_client.describe_volumes(
        Filters=[
            {"Name": "attachment.instance-id", "Values": [instance_id]}
        ]
    )

    volume_list = [
        {
            "Device": attachment["Device"],
            "VolumeId": volume["VolumeId"]
        }
        for volume in volumes["Volumes"]
        for attachment in volume["Attachments"]
    ]

    dashboard_body = {
        "widgets": []
    }

    for volume in volume_list:
        widget = {
            "type": "metric",
            "properties": {
                "metrics": [
                    [{"expression": "(m1+m2)/(PERIOD(m1)-m3)", "label": "Average IOPS/s when volume is active", "id": "e1", "region": region}],
                    [{"expression": "(m1+m2)/(PERIOD(m1))", "label": "Average IOPS/s", "id": "e2", "visible": False, "region": region}],
                    [{"expression": "(m4+m5)/(PERIOD(m4)-m3)", "label": "Average Throughput when volume is active", "id": "e3", "region": region}],
                    [{"expression": "(m4+m5)/(PERIOD(m4))", "label": "Average Throughput in bytes", "id": "e4", "visible": False, "region": region}],
                    ["AWS/EBS", "VolumeReadOps", "VolumeId", volume["VolumeId"], {"region": region, "id": "m1", "visible": False}],
                    ["AWS/EBS", "VolumeWriteOps", "VolumeId", volume["VolumeId"], {"region": region, "id": "m2", "visible": False}],
                    ["AWS/EBS", "VolumeIdleTime", "VolumeId", volume["VolumeId"], {"region": region, "id": "m3", "visible": False}],
                    ["AWS/EBS", "VolumeReadBytes", "VolumeId", volume["VolumeId"], {"region": region, "id": "m4", "visible": False}],
                    ["AWS/EBS", "VolumeWriteBytes", "VolumeId", volume["VolumeId"], {"region": region, "id": "m5", "visible": False}]
                ],
                "view": "timeSeries",
                "stacked": False,
                "region": region,
                "stat": "Sum",
                "period": 60,
                "title": volume["Device"]
            }
        }
        dashboard_body["widgets"].append(widget)

    cloudwatch_client.put_dashboard(
        DashboardName=formatted_name,
        DashboardBody=json.dumps(dashboard_body)
    )

    ebs_performance = []

    for volume in volume_list:
        volume_id = volume["VolumeId"]
        ebs_device = volume["Device"]
        ebs_info = next((item for item in volume_list if item["VolumeId"] == volume_id), {})
        ebs_iops = ebs_info.get("Iops", "N/A")
        ebs_throughput = ebs_info.get("Throughput", "N/A")

        ebs_performance.append(
            f"Volume {ebs_device}: {volume_id}, IOPS: {ebs_iops}, Throughput: {ebs_throughput} MB/s"
        )

    analysis_output.configure(state="normal")
    analysis_output.delete(1.0, tk.END)
    analysis_output.insert(tk.END, "\n".join(ebs_performance))
    analysis_output.configure(state="disabled")

    output_file = f"{instance_id}_ebs_performance.txt"
    with open(output_file, "w") as file:
        file.write("\n".join(ebs_performance))

    messagebox.showinfo("Success", f"EBS performance data saved to {output_file}")


def main():
    global profile_search_entry, profile_menu, instance_search_entry, tree, profiles, instances, selected_profile
    global command_os_var, instance_count_var, tunnel_target_var, command_target_var
    global localport_entry, remoteport_entry, command_input, command_output, analysis_output
    global selected_region, app_root
    global connectivity_rows, connectivity_results, connectivity_csv_path
    global connectivity_source_var, connectivity_csv_var, connectivity_timeout_var
    global connectivity_summary_var, connectivity_input_tree, connectivity_results_tree
    global connectivity_canvas, connectivity_detail_text

    root = tk.Tk()
    app_root = root
    root.title(f"{APP_NAME} for ")
    root.geometry("1280x760")
    root.minsize(900, 560)
    root.configure(bg=BG_COLOR)
    root.option_add("*Font", FONT)

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Treeview", font=FONT, background=TREE_BG, fieldbackground=TREE_BG, foreground=TREE_FG, rowheight=30, borderwidth=0)
    style.configure("Treeview.Heading", font=FONT_TITLE, background=PANEL_ALT_COLOR, foreground=ACCENT_COLOR, relief="flat")
    style.map("Treeview", background=[("selected", TREE_SELECTED)], foreground=[("selected", "#fff7d6")])
    style.configure("TLabel", font=FONT, background=BG_COLOR, foreground=TEXT_COLOR)
    style.configure("TButton", font=FONT, background=PANEL_ALT_COLOR, foreground=TEXT_COLOR, padding=(10, 6), borderwidth=0)
    style.map("TButton", background=[("active", "#3b2411")], foreground=[("active", "#ffffff")])
    style.configure("TCombobox", font=FONT, fieldbackground=ENTRY_BG, background=ENTRY_BG, foreground=ENTRY_FG, arrowsize=14)
    style.configure("TNotebook", background=BG_COLOR, borderwidth=0, tabmargins=(10, 8, 10, 0))
    style.configure("TNotebook.Tab", font=FONT, background=PANEL_DEEP_COLOR, foreground=MUTED_COLOR, padding=(18, 9), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", PANEL_ALT_COLOR), ("active", PANEL_COLOR)], foreground=[("selected", ACCENT_COLOR), ("active", TEXT_COLOR)])
    style.configure("TFrame", background=BG_COLOR)
    style.configure("Action.TButton", font=FONT, background="#7c3f16", foreground="#fff7d6", padding=(12, 7))
    style.map("Action.TButton", background=[("active", "#b85f1d")], foreground=[("active", "#ffffff")])
    style.configure("Secondary.TButton", font=FONT, background=PANEL_DEEP_COLOR, foreground=TEXT_COLOR, padding=(12, 7), borderwidth=0)
    style.map("Secondary.TButton", background=[("active", "#3b2411")], foreground=[("active", "#ffffff")])

    selected_profile = tk.StringVar()
    selected_region = tk.StringVar(value=DEFAULT_REGION)
    command_os_var = tk.StringVar(value="Linux")
    instance_count_var = tk.StringVar(value="No instances loaded")
    tunnel_target_var = tk.StringVar(value="No instance selected")
    command_target_var = tk.StringVar(value="No instance selected")
    connectivity_rows = []
    connectivity_results = []
    connectivity_csv_path = ""
    connectivity_source_var = tk.StringVar(value="No instance selected")
    connectivity_csv_var = tk.StringVar(value="No CSV loaded")
    connectivity_timeout_var = tk.StringVar(value="5")
    connectivity_summary_var = tk.StringVar(value="Load a CSV and select a source instance to begin.")
    connectivity_input_tree = None
    connectivity_results_tree = None
    connectivity_canvas = None
    connectivity_detail_text = None

    notebook = ttk.Notebook(root, style="TNotebook")
    notebook.pack(expand=True, fill="both", padx=6, pady=6)

    skin_path = get_skin_path()

    powercon_frame = create_tab_with_background(notebook, "Terminal", skin_path)
    powertunnel_frame = create_tab_with_background(notebook, "Tunneling", skin_path)
    powercommand_frame = create_tab_with_background(notebook, "Command", skin_path)
    powerebs_frame = create_tab_with_background(notebook, "Performance", skin_path)
    connectivity_frame = create_tab_with_background(notebook, "Connectivity Validation", skin_path)

    powercon_frame.grid_columnconfigure(0, weight=1)
    powercon_frame.grid_rowconfigure(1, weight=1)

    controls_frame = tk.Frame(
        powercon_frame,
        bg=PANEL_DEEP_COLOR,
        padx=16,
        pady=14,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
    )
    controls_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
    controls_frame.grid_columnconfigure(2, weight=1)
    controls_frame.grid_columnconfigure(5, weight=1)
    controls_frame.grid_columnconfigure(6, weight=1)

    title_label = tk.Label(controls_frame, text=APP_NAME, font=FONT_TITLE, bg=PANEL_DEEP_COLOR, fg=ACCENT_COLOR)
    title_label.grid(row=0, column=0, columnspan=2, padx=(0, 18), pady=(0, 12), sticky="w")

    count_label = tk.Label(controls_frame, textvariable=instance_count_var, font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=TEXT_COLOR)
    count_label.grid(row=0, column=2, pady=(0, 12), sticky="w")

    region_label = tk.Label(controls_frame, text="Region", font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=MUTED_COLOR)
    region_label.grid(row=0, column=3, padx=(0, 8), pady=(0, 12), sticky="e")
    region_menu = ttk.Combobox(controls_frame, textvariable=selected_region, values=AWS_REGIONS, font=FONT, state="readonly", width=16)
    region_menu.grid(row=0, column=4, columnspan=2, pady=(0, 12), sticky="w")

    profile_menu_label = tk.Label(controls_frame, text="Profile", font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=MUTED_COLOR)
    profile_menu_label.grid(row=1, column=0, padx=(0, 8), pady=4, sticky="w")

    profile_search_entry = tk.Entry(controls_frame, font=FONT, width=26, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG, relief="flat")
    profile_search_entry.grid(row=1, column=1, padx=(0, 10), pady=4, sticky="ew")
    profile_search_entry.bind("<KeyRelease>", filter_profiles)

    profile_menu = ttk.Combobox(controls_frame, textvariable=selected_profile, font=FONT, state="readonly", width=36)
    profile_menu.grid(row=1, column=2, padx=(0, 10), pady=4, sticky="ew")

    refresh_button = ttk.Button(controls_frame, text="Refresh", command=refresh_instances, style="Action.TButton")
    refresh_button.grid(row=1, column=3, padx=(0, 8), pady=4, sticky="ew")

    sync_button = ttk.Button(controls_frame, text="Validate", command=validate_session_async, style="Secondary.TButton")
    sync_button.grid(row=1, column=4, padx=(0, 0), pady=4, sticky="ew")

    profiles = get_profiles()
    if profiles:
        profile_menu["values"] = profiles
        profile_menu.set(profiles[0])
    else:
        messagebox.showerror("Wake Up!!!", "No se encontraron perfiles de AWS configurados.")

    instance_search_label = tk.Label(controls_frame, text="Instance", font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=MUTED_COLOR)
    instance_search_label.grid(row=2, column=0, padx=(0, 8), pady=(8, 0), sticky="w")

    instance_search_entry = tk.Entry(controls_frame, font=FONT, width=26, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG, relief="flat")
    instance_search_entry.grid(row=2, column=1, padx=(0, 10), pady=(8, 0), sticky="ew")
    instance_search_entry.bind("<KeyRelease>", filter_instances)

    connect_button = ttk.Button(controls_frame, text="Connect", command=connect_selected_instances, style="Action.TButton")
    connect_button.grid(row=2, column=2, padx=(0, 10), pady=(8, 0), sticky="w")

    banner_label = tk.Label(controls_frame, text="AWS SSM operations from arrakis", font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=MUTED_COLOR)
    banner_label.grid(row=2, column=3, columnspan=3, padx=(0, 0), pady=(8, 0), sticky="e")

    tree_frame = tk.Frame(powercon_frame, bg=BORDER_COLOR, padx=1, pady=1)
    tree_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
    tree_frame.grid_columnconfigure(0, weight=1)
    tree_frame.grid_rowconfigure(0, weight=1)

    tree = ttk.Treeview(tree_frame, columns=(CHECK_COLUMN, "Name", "InstanceID", "PrivateIP", "PlatformDetails", "InstanceState"), show="headings", style="Treeview")
    tree.heading(CHECK_COLUMN, text="☑", command=toggle_visible_instances_checked)
    tree.heading("Name", text="Name")
    tree.heading("InstanceID", text="Instance ID")
    tree.heading("PrivateIP", text="Private IP")
    tree.heading("PlatformDetails", text="OS")
    tree.heading("InstanceState", text="State")
    tree.column(CHECK_COLUMN, width=64, minwidth=56, anchor="center", stretch=False)
    tree.column("Name", width=360, minwidth=220, anchor="w")
    tree.column("InstanceID", width=200, minwidth=170, anchor="w")
    tree.column("PrivateIP", width=140, minwidth=120, anchor="w")
    tree.column("PlatformDetails", width=170, minwidth=150, anchor="w")
    tree.column("InstanceState", width=120, minwidth=100, anchor="center")
    tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=tree_scroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    tree_scroll.grid(row=0, column=1, sticky="ns")
    tree.bind("<Configure>", resize_instance_tree)
    tree.bind("<Button-1>", on_instance_tree_click)
    tree.bind("<<TreeviewSelect>>", update_selected_instance_context)
    tree.tag_configure("even", background=TREE_BG, foreground=TREE_FG)
    tree.tag_configure("odd", background=TREE_ALT_BG, foreground=TREE_FG)
    root.after(100, resize_instance_tree)

    tunnel_panel = tk.Frame(
        powertunnel_frame,
        bg=PANEL_DEEP_COLOR,
        padx=30,
        pady=26,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
    )
    tunnel_panel.place(relx=0.5, rely=0.07, anchor="n", width=500)
    tunnel_panel.grid_columnconfigure(1, weight=0)
    tk.Label(tunnel_panel, textvariable=tunnel_target_var, font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=TEXT_COLOR).grid(row=0, column=0, columnspan=3, pady=(0, 16), sticky="w")
    tk.Label(tunnel_panel, text="Local port", font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=MUTED_COLOR).grid(row=1, column=0, padx=(0, 12), pady=6, sticky="w")
    localport_entry = tk.Entry(tunnel_panel, font=FONT, width=PORT_WIDTH, justify="center", bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG, relief="flat")
    localport_entry.grid(row=1, column=1, padx=(0, 12), pady=6, sticky="w")
    tk.Label(tunnel_panel, text="Remote port", font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=MUTED_COLOR).grid(row=2, column=0, padx=(0, 12), pady=6, sticky="w")
    remoteport_entry = tk.Entry(tunnel_panel, font=FONT, width=PORT_WIDTH, justify="center", bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG, relief="flat")
    remoteport_entry.grid(row=2, column=1, padx=(0, 12), pady=6, sticky="w")
    ttk.Button(tunnel_panel, text="Start tunnel", command=open_tunnel_with_terminal, style="Action.TButton").grid(row=1, column=2, rowspan=2, padx=(8, 0), pady=6, sticky="ns")

    powercommand_frame.grid_columnconfigure(0, weight=1)
    powercommand_frame.grid_columnconfigure(1, weight=12)
    powercommand_frame.grid_columnconfigure(2, weight=1)
    powercommand_frame.grid_rowconfigure(2, weight=1)
    command_panel = tk.Frame(
        powercommand_frame,
        bg=PANEL_DEEP_COLOR,
        padx=14,
        pady=10,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
    )
    command_panel.grid(row=0, column=1, sticky="ew", padx=16, pady=(16, 10))
    command_panel.grid_columnconfigure(1, weight=1)
    tk.Label(command_panel, text="Command", font=FONT_TITLE, bg=PANEL_DEEP_COLOR, fg=ACCENT_COLOR).grid(row=0, column=0, sticky="w")
    tk.Label(command_panel, textvariable=command_target_var, font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=TEXT_COLOR).grid(row=0, column=1, padx=(18, 18), sticky="w")
    tk.Label(command_panel, text="Target OS", font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=MUTED_COLOR).grid(row=0, column=2, padx=(8, 8), sticky="e")
    command_os_menu = ttk.Combobox(command_panel, textvariable=command_os_var, font=FONT, state="readonly", values=("Linux", "Windows"), width=10)
    command_os_menu.grid(row=0, column=3, padx=(0, 10), sticky="e")
    ttk.Button(command_panel, text="Send command", command=invocation, style="Action.TButton").grid(row=0, column=4, padx=(0, 8), sticky="e")
    ttk.Button(command_panel, text="Clear output", command=clear_command_output, style="Secondary.TButton").grid(row=0, column=5, sticky="e")
    command_input = tk.Text(
        powercommand_frame,
        height=7,
        font=FONT,
        bg=OUTPUT_BG,
        fg=TEXT_COLOR,
        insertbackground=ACCENT_COLOR,
        relief="flat",
        padx=12,
        pady=10,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
    )
    command_input.grid(row=1, column=1, sticky="ew", padx=16, pady=(0, 10))
    command_output = tk.Text(
        powercommand_frame,
        font=FONT,
        bg=OUTPUT_BG,
        fg=LINK_COLOR,
        state="disabled",
        relief="flat",
        padx=12,
        pady=10,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
    )
    command_output.grid(row=2, column=1, sticky="nsew", padx=16, pady=(0, 16))

    powerebs_frame.grid_columnconfigure(0, weight=1)
    powerebs_frame.grid_columnconfigure(1, weight=12)
    powerebs_frame.grid_columnconfigure(2, weight=1)
    powerebs_frame.grid_rowconfigure(1, weight=1)
    ebs_toolbar = tk.Frame(
        powerebs_frame,
        bg=PANEL_DEEP_COLOR,
        padx=14,
        pady=10,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
    )
    ebs_toolbar.grid(row=0, column=1, sticky="ew", padx=16, pady=(16, 10))
    ebs_toolbar.grid_columnconfigure(0, weight=1)
    tk.Label(ebs_toolbar, text="EBS data analysis", font=FONT_TITLE, bg=PANEL_DEEP_COLOR, fg=ACCENT_COLOR).grid(row=0, column=0, sticky="w")
    ttk.Button(ebs_toolbar, text="Create dashboard", command=ebs_analysis, style="Action.TButton").grid(row=0, column=1, padx=(0, 8), sticky="e")
    ttk.Button(ebs_toolbar, text="Select CSV", command=seleccionar_archivo, style="Secondary.TButton").grid(row=0, column=2, sticky="e")
    analysis_output = tk.Text(
        powerebs_frame,
        font=FONT,
        bg=OUTPUT_BG,
        fg=TEXT_COLOR,
        insertbackground=ACCENT_COLOR,
        relief="flat",
        padx=12,
        pady=10,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
    )
    analysis_output.grid(row=1, column=1, sticky="nsew", padx=16, pady=(0, 16))
    analysis_output.insert(tk.END, "Select an EC2 instance and create an EBS dashboard, or load a CSV to calculate a baseline.\n")
    analysis_output.configure(state="disabled")

    connectivity_frame.grid_columnconfigure(0, weight=1)
    connectivity_frame.grid_rowconfigure(2, weight=2)
    connectivity_frame.grid_rowconfigure(3, weight=3)
    connectivity_frame.grid_rowconfigure(4, weight=2)

    connectivity_toolbar = tk.Frame(
        connectivity_frame,
        bg=PANEL_DEEP_COLOR,
        padx=14,
        pady=10,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
    )
    connectivity_toolbar.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
    connectivity_toolbar.grid_columnconfigure(1, weight=1)
    connectivity_toolbar.grid_columnconfigure(4, weight=1)

    tk.Label(connectivity_toolbar, text="Connectivity Validation", font=FONT_TITLE, bg=PANEL_DEEP_COLOR, fg=ACCENT_COLOR).grid(row=0, column=0, padx=(0, 18), sticky="w")
    tk.Label(connectivity_toolbar, textvariable=connectivity_summary_var, font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=TEXT_COLOR, wraplength=700, justify="left").grid(row=0, column=1, columnspan=6, sticky="w")

    tk.Label(connectivity_toolbar, text="Source", font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=MUTED_COLOR).grid(row=1, column=0, pady=(8, 0), sticky="w")
    tk.Label(connectivity_toolbar, textvariable=connectivity_source_var, font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=TEXT_COLOR).grid(row=1, column=1, pady=(8, 0), sticky="w")
    tk.Label(connectivity_toolbar, textvariable=connectivity_csv_var, font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=TEXT_COLOR).grid(row=1, column=2, columnspan=2, pady=(8, 0), sticky="w")
    tk.Label(connectivity_toolbar, text="Timeout (s)", font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=MUTED_COLOR).grid(row=1, column=4, pady=(8, 0), sticky="e")
    timeout_entry = tk.Entry(connectivity_toolbar, textvariable=connectivity_timeout_var, font=FONT, width=8, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG, relief="flat", justify="center")
    timeout_entry.grid(row=1, column=5, padx=(8, 10), pady=(8, 0), sticky="w")
    ttk.Button(connectivity_toolbar, text="Load CSV", command=load_connectivity_csv, style="Secondary.TButton").grid(row=1, column=6, padx=(0, 8), pady=(8, 0), sticky="e")
    ttk.Button(connectivity_toolbar, text="Run validation", command=run_connectivity_validation, style="Action.TButton").grid(row=1, column=7, pady=(8, 0), sticky="e")

    input_frame = tk.Frame(
        connectivity_frame,
        bg=PANEL_DEEP_COLOR,
        padx=12,
        pady=8,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
    )
    input_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
    input_frame.grid_columnconfigure(0, weight=1)
    tk.Label(input_frame, text="Loaded services", font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=ACCENT_COLOR).grid(row=0, column=0, sticky="w")
    connectivity_input_tree = ttk.Treeview(input_frame, columns=("Service", "Destination", "Port", "Scope", "Protocol"), show="headings", style="Treeview", height=5)
    for column_name, width in (("Service", 240), ("Destination", 280), ("Port", 90), ("Scope", 120), ("Protocol", 100)):
        connectivity_input_tree.heading(column_name, text=column_name)
        connectivity_input_tree.column(column_name, width=width, anchor="w")
    connectivity_input_tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
    input_scroll = ttk.Scrollbar(input_frame, orient="vertical", command=connectivity_input_tree.yview)
    connectivity_input_tree.configure(yscrollcommand=input_scroll.set)
    input_scroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))

    map_frame = tk.Frame(
        connectivity_frame,
        bg=PANEL_DEEP_COLOR,
        padx=12,
        pady=8,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
    )
    map_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 10))
    map_frame.grid_columnconfigure(0, weight=3)
    map_frame.grid_columnconfigure(1, weight=1)
    map_frame.grid_rowconfigure(0, weight=1)
    connectivity_canvas = tk.Canvas(map_frame, bg=OUTPUT_BG, highlightthickness=0, height=280)
    connectivity_canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    detail_panel = tk.Frame(map_frame, bg=OUTPUT_BG, padx=10, pady=10)
    detail_panel.grid(row=0, column=1, sticky="nsew")
    detail_panel.grid_rowconfigure(1, weight=1)
    tk.Label(detail_panel, text="Node details", font=FONT_SMALL, bg=OUTPUT_BG, fg=ACCENT_COLOR).grid(row=0, column=0, sticky="w")
    connectivity_detail_text = tk.Text(detail_panel, height=12, font=FONT_SMALL, bg=OUTPUT_BG, fg=TEXT_COLOR, insertbackground=ACCENT_COLOR, relief="flat", padx=8, pady=8)
    connectivity_detail_text.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
    connectivity_detail_text.configure(state="disabled")

    results_frame = tk.Frame(
        connectivity_frame,
        bg=PANEL_DEEP_COLOR,
        padx=12,
        pady=8,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
    )
    results_frame.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 16))
    results_frame.grid_columnconfigure(0, weight=1)
    results_frame.grid_rowconfigure(1, weight=1)
    tk.Label(results_frame, text="Validation results", font=FONT_SMALL, bg=PANEL_DEEP_COLOR, fg=ACCENT_COLOR).grid(row=0, column=0, sticky="w")
    results_tree_frame = tk.Frame(results_frame, bg=BORDER_COLOR, padx=1, pady=1)
    results_tree_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
    results_tree_frame.grid_columnconfigure(0, weight=1)
    results_tree_frame.grid_rowconfigure(0, weight=1)
    connectivity_results_tree = ttk.Treeview(
        results_tree_frame,
        columns=("Service", "Destination", "Port", "Scope", "Status", "Duration", "Detail"),
        show="headings",
        style="Treeview",
    )
    for column_name, width in (
        ("Service", 220),
        ("Destination", 240),
        ("Port", 80),
        ("Scope", 100),
        ("Status", 100),
        ("Duration", 100),
        ("Detail", 380),
    ):
        connectivity_results_tree.heading(column_name, text=column_name)
        connectivity_results_tree.column(column_name, width=width, anchor="w")
    connectivity_results_tree.grid(row=0, column=0, sticky="nsew")
    results_scroll = ttk.Scrollbar(results_tree_frame, orient="vertical", command=connectivity_results_tree.yview)
    connectivity_results_tree.configure(yscrollcommand=results_scroll.set)
    results_scroll.grid(row=0, column=1, sticky="ns")
    connectivity_results_tree.bind("<<TreeviewSelect>>", lambda _event: update_connectivity_detail_from_tree())
    connectivity_results_tree.tag_configure("ok", background="#12351a", foreground="#dcfce7")
    connectivity_results_tree.tag_configure("ko", background="#381619", foreground="#ffd7d7")

    def update_connectivity_detail_from_tree():
        selection = connectivity_results_tree.selection()
        if not selection:
            return
        values = connectivity_results_tree.item(selection[0]).get("values", [])
        if len(values) < 7:
            return
        result = {
            "service": values[0],
            "destination": values[1],
            "port": values[2],
            "scope": values[3],
            "status": values[4],
            "duration": values[5],
            "detail": values[6],
        }
        update_connectivity_detail(result)

    root.mainloop()


if __name__ == "__main__":
    main()
