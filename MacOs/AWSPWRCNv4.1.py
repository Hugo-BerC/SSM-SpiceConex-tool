import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import json
import time
import os
import sys
import shutil
import shlex
import html
import boto3
from PIL import Image, ImageTk
from tkinter import filedialog
import numpy as np
import pandas as pd
import configparser

APP_NAME = "SSM-PowerConnect"
DEFAULT_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "eu-west-1"


def log_error(message):
    print(f"ERROR: {message}")


def get_aws_cli_path():
    aws_path = shutil.which("aws")
    if aws_path:
        return aws_path
    for path in ("/opt/homebrew/bin/aws", "/usr/local/bin/aws", "/usr/bin/aws", "/bin/aws"):
        if os.path.isfile(path):
            return path
    return None


def run_command(command):
    try:
        if command.strip().startswith("aws "):
            aws_path = get_aws_cli_path()
            if not aws_path:
                log_error("AWS CLI not found in PATH.")
                messagebox.showerror("Wake Up!!!", "AWS CLI not found. Install AWS CLI v2 or ensure it is in PATH.")
                return ""
            command = command.replace("aws", aws_path, 1)
        print(f"Ejecutando comando: {command}")
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        log_error(f"Error ejecutando {command}: {e.stderr}")
        return ""


def open_terminal_command(command):
    # Terminal.app receives AppleScript source, so quote the shell command for
    # AppleScript independently from the shell quoting used to build it.
    apple_script_command = command.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    script = (
        "tell application \"Terminal\"\n"
        "    activate\n"
        "    if (count of windows) is 0 then\n"
        f"        do script \"{apple_script_command}\"\n"
        "    else\n"
        f"        do script \"{apple_script_command}\" in front window\n"
        "    end if\n"
        "end tell"
    )

    window_script = (
        "tell application \"Terminal\"\n"
        "    activate\n"
        f"    do script \"{escaped_command}\"\n"
        "end tell"
    )
    return subprocess.run(["osascript", "-e", script], check=False).returncode == 0


def get_skin_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    bundle_dir = getattr(sys, "_MEIPASS", None)
    candidates = [
        os.path.join(script_dir, "skin.jpg"),
        os.path.join(script_dir, "..", "Windows", "skin.jpg"),
    ]
    if bundle_dir:
        candidates.insert(0, os.path.join(bundle_dir, "skin.jpg"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def get_profiles():
    output = run_command("aws configure list-profiles")
    if not output:
        return []
    profiles = output.splitlines()
    return sorted(profiles)


def load_profile_account_ids():
    config_path = os.path.expanduser("~/.aws/config")
    parser = configparser.ConfigParser()
    if not os.path.isfile(config_path):
        return {}
    parser.read(config_path)
    account_ids = {}
    for section in parser.sections():
        if section == "default":
            profile_name = "default"
        elif section.startswith("profile "):
            profile_name = section.replace("profile ", "", 1)
        else:
            profile_name = section
        account_id = parser.get(section, "sso_account_id", fallback=None)
        if account_id:
            account_ids[profile_name] = account_id
    return account_ids


def get_profile_account_id(profile):
    return profile_account_ids.get(profile)


def fetch_account_id_for_profile(profile):
    output = run_command(f"aws sts get-caller-identity --profile {shlex.quote(profile)}")
    if not output:
        return None
    try:
        return json.loads(output).get("Account")
    except json.JSONDecodeError:
        return None


def sync_account_ids():
    config_path = os.path.expanduser("~/.aws/config")
    parser = configparser.ConfigParser()
    if os.path.isfile(config_path):
        parser.read(config_path)

    updated = 0
    for profile in profiles:
        if get_profile_account_id(profile):
            continue

        account_id = fetch_account_id_for_profile(profile)
        if not account_id:
            continue

        if profile == "default":
            section = "default"
        else:
            section = f"profile {profile}"

        if not parser.has_section(section):
            parser.add_section(section)

        parser.set(section, "sso_account_id", account_id)
        profile_account_ids[profile] = account_id
        updated += 1

    if updated:
        with open(config_path, "w") as config_file:
            parser.write(config_file)

    messagebox.showinfo("Sync Account IDs", f"Updated {updated} profile(s).")


def sync_account_ids_async():
    threading.Thread(target=sync_account_ids, daemon=True).start()


def validate_session(profile):
    return bool(run_command(f"aws sts get-caller-identity --profile {shlex.quote(profile)}"))


def login_sso(profile):
    aws_path = get_aws_cli_path() or "aws"
    open_terminal_command(f"{shlex.quote(aws_path)} sso login --profile {shlex.quote(profile)}")


def get_instances(profile):
    try:
        session = boto3.Session(profile_name=profile, region_name=DEFAULT_REGION)

        ec2_client = session.client('ec2')
        response = ec2_client.describe_instances()

        formatted_instances = []
        print(f"Getting instances from profile: {profile}")

        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                tags = instance.get('Tags', [])
                name = next((tag['Value'] for tag in tags if tag['Key'] == 'Name'), "No Name")
                formatted_instances.append({
                    'Name': name,
                    'InstanceID': instance['InstanceId'],
                    'PrivateIpAddress': instance.get('PrivateIpAddress', 'No IP'),
                    'InstanceState': instance['State']['Name']
                })

        return formatted_instances

    except Exception as e:
        log_error(f"Error getting instances from profile {profile}")
        raise RuntimeError(f"Cannot get instances from profile {profile}: {e}") from e


def filter_profiles(event):
    search_text = profile_search_entry.get().lower()
    filtered_profiles = []
    check_account_id = search_text.isdigit() and len(search_text) >= 4
    for profile in profiles:
        if search_text in profile.lower():
            filtered_profiles.append(profile)
            continue
        if check_account_id:
            account_id = get_profile_account_id(profile)
            if account_id and search_text in account_id:
                filtered_profiles.append(profile)
    profile_menu['values'] = filtered_profiles

    if filtered_profiles:
        profile_menu.current(0)
    else:
        profile_menu.set('')


def filter_instances(event):
    search_text = instance_search_entry.get().lower()
    filtered_instances = [inst for inst in instances if search_text in str(inst['Name']).lower() or search_text in str(inst['InstanceID'])]

    for row in tree.get_children():
        tree.delete(row)

    for inst in filtered_instances:
        tree.insert("", "end", values=(inst['Name'] or 'No Name', inst['InstanceID'], inst['PrivateIpAddress'], inst['InstanceState']))


def refresh_instances():
    global instances
    profile = selected_profile.get()
    if not profile:
        messagebox.showerror("Wake Up!!!", "Select valid profile.")
        return

    instances = []  # Limpiar siempre antes de cargar

    try:
        instances = get_instances(profile)
    except Exception as e:
        messagebox.showerror("Wake Up!!!", "Session outdated or not logged in. Please perform SSO login")
        login_sso(profile)
        print(e)
        return

    if not instances:
        messagebox.showerror("Wake Up!!!", f"Instances not found on: {profile}")
        return

    for row in tree.get_children():
        tree.delete(row)

    for inst in instances:
        tree.insert("", "end", values=(
            inst.get('Name', 'No Name'),
            inst['InstanceID'],
            inst.get('PrivateIpAddress', 'No IP'),
            inst.get('InstanceState')
        ))


def connect_selected_instances():
    profile = selected_profile.get()
    selected_items = tree.selection()
    instance_ids = [tree.item(item)['values'][1] for item in selected_items]
    if not instance_ids:
        typed_id = instance_search_entry.get().strip()
        if typed_id.startswith("i-"):
            instance_ids = [typed_id]
        else:
            messagebox.showerror("Wake Up!!!", "Select an instance or type a valid Instance ID (i-...).")
            return
    threading.Thread(target=connect_to_instance, args=(profile, instance_ids), daemon=True).start()


def connect_to_instance(profile, instance_ids):
    """Inicia una sesion SSM en la instancia seleccionada."""
    try:
        if not instance_ids:
            messagebox.showerror("Wake Up!!!", "No instances selected.")
            return

        for instance_id in instance_ids:
            aws_path = get_aws_cli_path() or "aws"
            command = (
                f"{shlex.quote(aws_path)} ssm start-session "
                f"--target {shlex.quote(instance_id)} --profile {shlex.quote(profile)}"
            )
            open_terminal_command(command)

    except Exception as e:
        log_error(f"Error al conectar a la instancia: {e}")
        messagebox.showerror("Wake Up!!!", f"Cannot connect to instance:\n{e}")


def open_tunnel_with_terminal():
    """Abre un tunel en la instancia seleccionada con parametros especificos."""
    try:
        selected_items = tree.selection()
        if selected_items:
            instance_id = tree.item(selected_items[0])['values'][1]
        else:
            instance_id = instance_search_entry.get().strip()
            if not instance_id.startswith("i-"):
                messagebox.showerror("Wake Up!!!", "Select an instance or type a valid Instance ID (i-...).")
                return
        profile = selected_profile.get()

        if not profile:
            messagebox.showerror("Wake Up!!!", "Select profile")
            return

        localport = localport_entry.get()
        remoteport = remoteport_entry.get()

        if not localport or not remoteport:
            messagebox.showerror("Wake Up!!!", " Type LocalPort and RemotePort.")
            return
        if not localport.isdigit() or not remoteport.isdigit():
            messagebox.showerror("Wake Up!!!", "LocalPort and RemotePort must be numeric.")
            return

        aws_path = get_aws_cli_path() or "aws"
        command = (
            f"{shlex.quote(aws_path)} ssm start-session --target "
            f"{shlex.quote(instance_id)} --profile {shlex.quote(profile)} "
            "--document-name AWS-StartPortForwardingSession "
            f"--parameters portNumber={shlex.quote(remoteport)},localPortNumber={shlex.quote(localport)}"
        )

        open_terminal_command(command)

    except Exception as e:
        log_error(f"Error al abrir tunel: {e}")
        messagebox.showerror("Wake Up!!!", f"No se pudo abrir el tunel:\n{e}")


def invocation():
    try:
        selected_items = tree.selection()
        if not selected_items:
            raise ValueError("Debe seleccionar al menos una instancia.")

        instance_ids = [tree.item(item)['values'][1] for item in selected_items]
        command = command_input.get("1.0", tk.END).strip()
        if not command:
            raise ValueError("Debe ingresar un comando para ejecutar.")

        threading.Thread(target=sendcommand, args=(instance_ids, command, selected_profile.get()), daemon=True).start()
        print(instance_ids)
    except Exception as e:
        messagebox.showerror("Wake Up!!!", str(e))


def sendcommand(instance_ids, command, profile):
    print(instance_ids)
    output = []
    for instance in instance_ids:
        try:
            print(f"Sending command on instance: {instance}")
            session = boto3.Session(profile_name=profile)

            ssm_client = session.client('ssm')

            response = ssm_client.send_command(
                InstanceIds=[instance],
                DocumentName="AWS-RunShellScript",
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

            instance_output = f"Instance {instance}:\n{standard_output}"
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


# Connectivity Validation ----------------------------------------------------
# The checks run on the managed instance through SSM; macOS is only the UI.
def load_connectivity_csv():
    path = filedialog.askopenfilename(title="Select connectivity CSV", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
    if not path:
        return
    try:
        dataframe = pd.read_csv(path)
        fields = {str(column).strip().lower().replace("_", ""): column for column in dataframe.columns}
        def column(*names):
            for name in names:
                if name in fields:
                    return fields[name]
            raise ValueError(f"Missing CSV column. Expected one of: {', '.join(names)}")
        service, destination = column("servicename", "service", "servicio", "name"), column("destination", "destino", "host", "target")
        port, scope = column("port", "puerto"), column("scope", "tipo", "type")
        rows = []
        for index, item in dataframe.iterrows():
            values = [str(item[field]).strip() for field in (service, destination, port, scope)]
            if not all(values) or any("|" in value or "\n" in value for value in values):
                raise ValueError(f"Invalid connectivity row {index + 2}")
            if not values[2].isdigit() or not 1 <= int(values[2]) <= 65535:
                raise ValueError(f"Invalid port in row {index + 2}: {values[2]}")
            rows.append(dict(service=values[0], destination=values[1], port=values[2], scope=values[3].lower()))
        if not rows:
            raise ValueError("The CSV contains no validation targets.")
        globals()["connectivity_rows"] = rows
        connectivity_csv_var.set(f"{os.path.basename(path)} · {len(rows)} services")
        for item in connectivity_input_tree.get_children(): connectivity_input_tree.delete(item)
        for row in rows: connectivity_input_tree.insert("", tk.END, values=(row["service"], row["destination"], row["port"], row["scope"]))
        connectivity_summary_var.set("CSV loaded. Select one source instance and run validation.")
    except Exception as exc:
        messagebox.showerror("Connectivity CSV", str(exc))


def connectivity_script(rows, timeout_seconds):
    targets = "\n".join(f"{row['service']}|{row['destination']}|{row['port']}|{row['scope']}" for row in rows)
    return f'''set +e
NC_BIN="$(command -v nc || command -v ncat || true)"
[ -z "$NC_BIN" ] && printf 'ERROR|nc not found|0|unknown|KO|0|nc command not found|unavailable\\n' && exit 0
while IFS='|' read -r service destination port scope; do
  start=$(date +%s%3N 2>/dev/null || date +%s) ; output=$("$NC_BIN" -vz -w {timeout_seconds} "$destination" "$port" 2>&1) ; code=$? ; end=$(date +%s%3N 2>/dev/null || date +%s)
  duration=$((end-start)); [ "$duration" -lt 100 ] && duration=$((duration*1000))
  status=KO; detail=$(printf '%s' "$output" | tr '\\r\\n|' '   '); hop=unavailable
  if [ "$code" -eq 0 ]; then status=OK; detail=connected; hop=connected
  elif getent ahosts "$destination" >/dev/null 2>&1; then
    case "$detail" in *timed*out*|*TIMEOUT*) status=TIMEOUT;; *refused*|*Refused*) status=REFUSED;; *unreachable*|*Unreachable*) status=UNREACHABLE;; esac
  else status=DNS; detail='DNS resolution failed'; hop=dns-unresolved; fi
  printf '%s|%s|%s|%s|%s|%s|%s|%s\\n' "$service" "$destination" "$port" "$scope" "$status" "$duration" "$detail" "$hop"
done <<'EOF_CONNECTIVITY'
{targets}
EOF_CONNECTIVITY'''


def parse_connectivity_output(output):
    results = []
    for line in output.splitlines():
        fields = line.split("|", 7)
        if len(fields) == 8:
            results.append(dict(zip(("service", "destination", "port", "scope", "status", "duration", "detail", "last_hop"), fields)))
    return results


def render_connectivity_results(results, source, profile):
    globals()["connectivity_results"] = results
    globals()["connectivity_context"] = dict(source=source, profile=profile, region=DEFAULT_REGION)
    for item in connectivity_results_tree.get_children(): connectivity_results_tree.delete(item)
    ok = 0
    for row in results:
        success = row["status"] == "OK"; ok += success
        connectivity_results_tree.insert("", tk.END, values=(row["service"], row["destination"], row["port"], row["scope"], row["status"], row["duration"], row["detail"]), tags=("ok" if success else "ko",))
    connectivity_summary_var.set(f"Validated {len(results)} services · OK {ok} · Failed {len(results)-ok} · {source}")


def execute_connectivity_validation(profile, instance_id, source, rows, timeout):
    try:
        client = boto3.Session(profile_name=profile, region_name=DEFAULT_REGION).client("ssm")
        response = client.send_command(InstanceIds=[instance_id], DocumentName="AWS-RunShellScript", Parameters={"commands": [connectivity_script(rows, timeout)]}, TimeoutSeconds=max(60, len(rows) * (timeout + 2)))
        command_id = response["Command"]["CommandId"]
        while True:
            time.sleep(1)
            invocation = client.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            if invocation["Status"] not in ("Pending", "InProgress", "Delayed"):
                break
        results = parse_connectivity_output(invocation.get("StandardOutputContent", ""))
        if not results: raise RuntimeError(invocation.get("StandardErrorContent") or f"SSM command ended with {invocation['Status']} and no parsable results.")
        root.after(0, lambda: render_connectivity_results(results, source, profile))
    except Exception as exc:
        root.after(0, lambda: messagebox.showerror("Connectivity validation", str(exc)))


def run_connectivity_validation():
    try:
        rows = globals().get("connectivity_rows", [])
        selected = tree.selection()
        if len(selected) != 1: raise ValueError("Select exactly one source instance in PowerCon.")
        if not rows: raise ValueError("Load a connectivity CSV first.")
        timeout = int(connectivity_timeout_var.get())
        if not 1 <= timeout <= 60: raise ValueError("Timeout must be between 1 and 60 seconds.")
        values = tree.item(selected[0])["values"]; instance_id, source = values[1], values[0]
        connectivity_summary_var.set(f"Running validation from {source}...")
        threading.Thread(target=execute_connectivity_validation, args=(selected_profile.get(), instance_id, source, rows, timeout), daemon=True).start()
    except Exception as exc:
        messagebox.showerror("Connectivity validation", str(exc))


def export_connectivity_report():
    results = globals().get("connectivity_results", [])
    if not results:
        messagebox.showerror("Connectivity export", "Run a connectivity validation first."); return
    path = filedialog.asksaveasfilename(title="Export connectivity report", defaultextension=".html", initialfile="connectivity-validation-report.html", filetypes=[("HTML files", "*.html")])
    if not path: return
    context = globals()["connectivity_context"]
    ok = sum(row["status"] == "OK" for row in results)
    table = "".join("<tr class='%s'>%s</tr>" % ("ok" if row["status"] == "OK" else "failed", "".join(f"<td>{html.escape(row[key])}</td>" for key in ("service", "destination", "port", "scope", "status", "duration", "detail"))) for row in results)
    routes = "".join(f"<div class='route {'ok' if row['status'] == 'OK' else 'failed'}'><strong>{html.escape(row['service'])}</strong><span>{html.escape(row['destination'])}:{html.escape(row['port'])}</span><small>{html.escape(row['status'])} · {html.escape(row['scope'])}</small></div>" for row in results)
    report = f"""<!doctype html><html><head><meta charset='utf-8'><title>Connectivity validation report</title><style>body{{background:#120d09;color:#f5dfb1;font:14px monospace;margin:32px}}h1,h2,strong{{color:#f6c453}}.card{{display:inline-block;border:1px solid #735738;padding:12px 22px;margin:0 8px 20px 0}}.map{{display:flex;gap:24px;border:1px solid #735738;padding:20px;background:#0b0d10}}.source{{border:2px solid #f6c453;padding:20px;min-width:150px;text-align:center;background:#2a1a0e}}.routes{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px;flex:1}}.route{{border-left:4px solid #e53935;padding:10px;background:#1c130c}}.route.ok{{border-color:#4caf50}}.route span,.route small{{display:block;margin-top:4px;color:#d6aa72}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px;text-align:left;border-bottom:1px solid #735738}}th{{color:#f6c453}}tr.ok td{{background:#12351a}}tr.failed td{{background:#381619}}</style></head><body><h1>Connectivity validation report</h1><p>Source: {html.escape(context['source'])} · Profile: {html.escape(context['profile'])} · Region: {html.escape(context['region'])}</p><div class='card'>Services<br><strong>{len(results)}</strong></div><div class='card'>OK<br><strong>{ok}</strong></div><div class='card'>Failed<br><strong>{len(results)-ok}</strong></div><h2>Connectivity map</h2><div class='map'><div class='source'><strong>SOURCE</strong><br>{html.escape(context['source'])}</div><div class='routes'>{routes}</div></div><h2>Validation results</h2><table><tr><th>Service</th><th>Destination</th><th>Port</th><th>Scope</th><th>Status</th><th>Duration (ms)</th><th>Detail</th></tr>{table}</table></body></html>"""
    with open(path, "w", encoding="utf-8") as handle: handle.write(report)
    messagebox.showinfo("Connectivity export", f"Report exported to:\n{path}")


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
    frame = ttk.Frame(notebook, style="TFrame")
    notebook.add(frame, text=text)

    if not image_path or not os.path.isfile(image_path):
        return frame

    bg_image = Image.open(image_path)
    bg_photo = ImageTk.PhotoImage(bg_image.resize((1920, 1080)))

    background_label = tk.Label(frame, image=bg_photo)
    background_label.image = bg_photo
    background_label.place(relwidth=1, relheight=1)

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
        selected_items = tree.selection()
        if not selected_items:
            messagebox.showerror("Wake Up!!!", "No instances selected")
            return

        name = tree.item(selected_items[0])['values'][0]
        formatted_name = "EBS_Analysis_" + name.split('.')[0]
        instance_id = tree.item(selected_items[0])['values'][1]
        profile = selected_profile.get()

        if not profile:
            messagebox.showerror("Wake Up!!!", "Select profile")
            return

    except Exception as e:
        log_error(f"Cannot create dashboard: {e}")
        messagebox.showerror("Wake Up!!!", f"Cannot create dashboard:\n{e}")

    region = DEFAULT_REGION
    session = boto3.Session(profile_name=profile, region_name=region)
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
        ebs_info = ec2_client.describe_volumes(
            Filters=[
                {"Name": "volume-id", "Values": [volume_id]}
            ])
        ebs_iops = ebs_info["Volumes"][0].get("Iops", "N/A")
        ebs_throughput = ebs_info["Volumes"][0].get("Throughput", "N/A")

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
    global profile_account_ids
    global localport_entry, remoteport_entry, command_input, command_output, analysis_output
    global root, connectivity_rows, connectivity_results, connectivity_csv_var, connectivity_timeout_var
    global connectivity_summary_var, connectivity_input_tree, connectivity_results_tree

    root = tk.Tk()
    root.title(f"{APP_NAME} for macOS")
    root.geometry("1440x740")

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Treeview", font=("Consolas", 12), background="black", fieldbackground="black", foreground="#258EFE", highlightthickness=0, bd=0)
    style.configure("Treeview.Heading", font=("Consolas", 12), background="#333333", foreground="white")
    style.configure("TLabel", font=("Consolas", 12), background="#2E2E2E", foreground="green")
    style.configure("TButton", font=("Consolas", 12), background="#3E3E3E", foreground="black", padding=5)
    style.configure("TCombobox", font=("Consolas", 14), background="black", foreground="black", selectbackground="#444444", selectforeground="white")
    style.configure("TEntry", font=("Consolas", 12), background="black", foreground="white")
    style.configure("TNotebook", background="#2E2E2E", borderwidth=0)
    style.configure("TNotebook.Tab", font=("Consolas", 12), background="#2E2E2E", foreground="black")
    style.configure("TFrame", background="black")
    style.configure("Action.TButton", background="black", foreground="yellow")
    style.map("Action.TButton", background=[("active", "#222222")], foreground=[("active", "yellow")])

    selected_profile = tk.StringVar()
    profile_account_ids = load_profile_account_ids()

    notebook = ttk.Notebook(root, style="TNotebook")
    notebook.pack(expand=True, fill="both")

    skin_path = get_skin_path()

    powercon_frame = create_tab_with_background(notebook, "PowerCon", skin_path)
    powertunnel_frame = create_tab_with_background(notebook, "PowerTunnel", skin_path)
    powercommand_frame = create_tab_with_background(notebook, "PowerCommand", skin_path)
    powerebs_frame = create_tab_with_background(notebook, "PowerEBS", skin_path)
    connectivity_frame = create_tab_with_background(notebook, "Connectivity", skin_path)

    connectivity_rows = []
    connectivity_results = []
    connectivity_csv_var = tk.StringVar(value="No CSV loaded")
    connectivity_timeout_var = tk.StringVar(value="5")
    connectivity_summary_var = tk.StringVar(value="Load a CSV and select one source instance in PowerCon.")

    profile_frame = tk.Frame(powercon_frame, bg="black", padx=5, pady=5)
    profile_frame.place(x=20, y=20, relwidth=0.95)

    profile_menu_label = tk.Label(profile_frame, text="Search profile: ", font=("Consolas", 12), bg="black", fg="yellow")
    profile_menu_label.grid(row=0, column=0, padx=5, sticky="w")

    profile_search_entry = tk.Entry(profile_frame, font=("Consolas", 12), width=30, bg="white", fg="black", insertbackground="black")
    profile_search_entry.grid(row=0, column=1, padx=5, sticky="w")
    profile_search_entry.bind("<KeyRelease>", filter_profiles)

    profile_menu = ttk.Combobox(profile_frame, textvariable=selected_profile, font=("Consolas", 12), state="readonly", width=55)
    profile_menu.grid(row=0, column=2, padx=5, sticky="w")
    profile_menu.bind("<<ComboboxSelected>>", lambda event: threading.Thread(target=refresh_instances).start())

    refresh_button = ttk.Button(profile_frame, text="Refresh Instances", command=refresh_instances, style="Action.TButton")
    refresh_button.grid(row=0, column=3, padx=5, sticky="e")

    sync_button = ttk.Button(profile_frame, text="Sync Account IDs", command=sync_account_ids_async, style="Action.TButton")
    sync_button.grid(row=0, column=4, padx=5, sticky="e")

    profiles = get_profiles()
    if profiles:
        profile_menu["values"] = profiles
        profile_menu.set(profiles[0])
    else:
        messagebox.showerror("Wake Up!!!", "No se encontraron perfiles de AWS configurados.")

    instance_frame = tk.Frame(powercon_frame, bg="black")
    instance_frame.place(x=20, y=60, relwidth=0.95)

    instance_search_label = tk.Label(instance_frame, text="Search instances:", font=("Consolas", 12), bg="black", fg="yellow")
    instance_search_label.grid(row=1, column=0, padx=5, sticky="w")

    instance_search_entry = tk.Entry(instance_frame, font=("Consolas", 12), width=30, bg="white", fg="black", insertbackground="black")
    instance_search_entry.grid(row=1, column=1, padx=5, sticky="w")
    instance_search_entry.bind("<KeyRelease>", filter_instances)

    connect_button = ttk.Button(instance_frame, text="Connection", command=connect_selected_instances, style="Action.TButton")
    connect_button.grid(row=1, column=2, padx=5, sticky="e")

    banner_label = tk.Label(instance_frame, text=f"       * {APP_NAME} on macOS *        region: {DEFAULT_REGION}        ", font=("Consolas", 12), bg="black", fg="yellow")
    banner_label.grid(row=1, column=3, padx=5, sticky="w")

    tree_frame = ttk.Frame(powercon_frame, style="TFrame")
    tree_frame.place(x=20, y=120, relwidth=0.95, relheight=0.7)

    tree = ttk.Treeview(tree_frame, columns=("Name", "InstanceID", "PrivateIP", "InstanceState"), show="headings", style="Treeview")
    tree.heading("Name", text="Name")
    tree.heading("InstanceID", text="Instance ID")
    tree.heading("PrivateIP", text="Private IP")
    tree.heading("InstanceState", text="State")
    tree.pack(expand=True, fill="both")

    _last_dclick_time = [0]

    def on_double_click(event):
        now = time.time()
        if now - _last_dclick_time[0] < 1.0:
            return "break"
        _last_dclick_time[0] = now
        item = tree.identify_row(event.y)
        if item:
            tree.selection_set(item)
            instance_id = tree.item(item)['values'][1]
            profile = selected_profile.get()
            threading.Thread(target=connect_to_instance, args=(profile, [instance_id])).start()
        return "break"

    tree.bind("<Double-1>", on_double_click)

    tk.Label(powertunnel_frame, text="Local Port:", font=("Consolas", 13), bg="#1E1E1E", fg="yellow").pack(pady=7)
    localport_entry = tk.Entry(powertunnel_frame, font=("Consolas", 13), bg="white", fg="black", insertbackground="black")
    localport_entry.pack(pady=5)

    tk.Label(powertunnel_frame, text="Remote Port:", font=("Consolas", 13), bg="#1E1E1E", fg="yellow").pack(pady=7)
    remoteport_entry = tk.Entry(powertunnel_frame, font=("Consolas", 13), bg="white", fg="black", insertbackground="black")
    remoteport_entry.pack(pady=5)

    ttk.Button(powertunnel_frame, text="Start Tunnel", command=open_tunnel_with_terminal, style="Action.TButton").pack(pady=7)

    tk.Label(powercommand_frame, text="Command:", font=("Consolas", 13), bg="#1E1E1E", fg="yellow").pack(pady=6)
    command_input = tk.Text(powercommand_frame, height=7, width=145, font=("Consolas", 12), bg="black", fg="white")
    command_input.pack(pady=5)

    ttk.Button(powercommand_frame, text="Send command", command=invocation, style="Action.TButton").pack(pady=6)

    command_output = tk.Text(powercommand_frame, height=38, width=145, font=("Consolas", 12), bg="black", fg="#258EFE", state="disabled")
    command_output.pack(pady=5)

    tk.Label(powerebs_frame, text="EBS Data Analysis", font=("Consolas", 13), bg="#1E1E1E", fg="yellow").pack(pady=6)
    ttk.Button(powerebs_frame, text="Create Dashboard", command=ebs_analysis, style="Action.TButton").pack(pady=6)
    ttk.Button(powerebs_frame, text="Select CSV file", command=seleccionar_archivo, style="Action.TButton").pack(pady=6)
    analysis_output = tk.Text(powerebs_frame, height=40, width=120, font=("Consolas", 12), bg="black", fg="white", insertbackground="white")
    analysis_output.pack(padx=20, pady=10)

    connectivity_controls = tk.Frame(connectivity_frame, bg="black", padx=12, pady=10)
    connectivity_controls.pack(fill="x", padx=18, pady=(16, 6))
    tk.Label(connectivity_controls, text="Connectivity Validation", font=("Consolas", 13), bg="black", fg="yellow").grid(row=0, column=0, padx=5, sticky="w")
    tk.Label(connectivity_controls, textvariable=connectivity_summary_var, font=("Consolas", 10), bg="black", fg="white", wraplength=820, justify="left").grid(row=0, column=1, columnspan=5, padx=8, sticky="w")
    tk.Label(connectivity_controls, textvariable=connectivity_csv_var, font=("Consolas", 10), bg="black", fg="#258EFE").grid(row=1, column=0, columnspan=2, padx=5, pady=8, sticky="w")
    tk.Label(connectivity_controls, text="Timeout (s):", font=("Consolas", 10), bg="black", fg="yellow").grid(row=1, column=2, padx=(18, 3))
    tk.Entry(connectivity_controls, textvariable=connectivity_timeout_var, width=5, font=("Consolas", 10), bg="white", fg="black").grid(row=1, column=3)
    ttk.Button(connectivity_controls, text="Load CSV", command=load_connectivity_csv, style="Action.TButton").grid(row=1, column=4, padx=8)
    ttk.Button(connectivity_controls, text="Run validation", command=run_connectivity_validation, style="Action.TButton").grid(row=1, column=5, padx=4)
    ttk.Button(connectivity_controls, text="Export HTML", command=export_connectivity_report, style="Action.TButton").grid(row=1, column=6, padx=4)

    input_frame = tk.Frame(connectivity_frame, bg="black", padx=12, pady=8)
    input_frame.pack(fill="both", expand=True, padx=18, pady=4)
    tk.Label(input_frame, text="Targets from CSV", font=("Consolas", 11), bg="black", fg="yellow").pack(anchor="w")
    connectivity_input_tree = ttk.Treeview(input_frame, columns=("Service", "Destination", "Port", "Scope"), show="headings", height=7, style="Treeview")
    for column, width in (("Service", 280), ("Destination", 500), ("Port", 80), ("Scope", 120)):
        connectivity_input_tree.heading(column, text=column); connectivity_input_tree.column(column, width=width)
    connectivity_input_tree.pack(fill="both", expand=True, pady=4)

    results_frame = tk.Frame(connectivity_frame, bg="black", padx=12, pady=8)
    results_frame.pack(fill="both", expand=True, padx=18, pady=4)
    tk.Label(results_frame, text="Validation results", font=("Consolas", 11), bg="black", fg="yellow").pack(anchor="w")
    connectivity_results_tree = ttk.Treeview(results_frame, columns=("Service", "Destination", "Port", "Scope", "Status", "Duration", "Detail"), show="headings", height=10, style="Treeview")
    for column, width in (("Service", 220), ("Destination", 330), ("Port", 70), ("Scope", 100), ("Status", 100), ("Duration", 100), ("Detail", 450)):
        connectivity_results_tree.heading(column, text=column); connectivity_results_tree.column(column, width=width)
    connectivity_results_tree.tag_configure("ok", background="#12351a", foreground="#dcfce7")
    connectivity_results_tree.tag_configure("ko", background="#381619", foreground="#ffd7d7")
    connectivity_results_tree.pack(fill="both", expand=True, pady=4)

    root.mainloop()


if __name__ == "__main__":
    main()
