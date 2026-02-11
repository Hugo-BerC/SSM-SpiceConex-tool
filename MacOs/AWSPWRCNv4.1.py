import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import json
import time
import os
import sys
import shutil
import boto3
from PIL import Image, ImageTk
from tkinter import filedialog
import numpy as np
import pandas as pd


def log_error(message):
    print(f"ERROR: {message}")


def get_aws_cli_path():
    aws_path = shutil.which("aws")
    if aws_path:
        return aws_path
    for path in ("/opt/homebrew/bin/aws", "/usr/local/bin/aws", "/usr/bin/aws"):
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
    script = (
        "tell application \"Terminal\"\n"
        "    if (count of windows) is 0 then\n"
        f"        do script \"{command}\"\n"
        "    else\n"
        f"        do script \"{command}\" in front window\n"
        "    end if\n"
        "    activate\n"
        "end tell"
    )
    subprocess.run(["osascript", "-e", script], check=False)


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


def validate_session(profile):
    return run_command(f"aws sts get-caller-identity --profile {profile}") is not None


def login_sso(profile):
    aws_path = get_aws_cli_path() or "aws"
    open_terminal_command(f"{aws_path} sso login --profile {profile}")


def get_instances(profile):
    try:
        session = boto3.Session(profile_name=profile, region_name="eu-west-1")

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
        print(e)
        return e


def filter_profiles(event):
    search_text = profile_search_entry.get().lower()
    filtered_profiles = [profile for profile in profiles if search_text in profile.lower()]
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
        if 'ERROR' in instances:
            messagebox.showerror("Wake Up!!!", "Session outdated or not logged in. Please perform SSO login")
            login_sso(profile)

    except Exception as e:
        messagebox.showerror("Wake Up!!!", "Session outdated or not logged in. Please perform SSO login")
        login_sso(profile)
        print(e)

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
    threading.Thread(target=connect_to_instance, args=(profile, instance_ids)).start()


def connect_to_instance(profile, instance_ids):
    """Inicia una sesion SSM en la instancia seleccionada."""
    try:
        if not instance_ids:
            messagebox.showerror("Wake Up!!!", "No instances selected.")
            return

        for instance_id in instance_ids:
            command = f"aws ssm start-session --target {instance_id} --profile {profile}"
            open_terminal_command(command)

    except Exception as e:
        log_error(f"Error al conectar a la instancia: {e}")
        messagebox.showerror("Wake Up!!!", f"Cannot connect to instance:\n{e}")


def open_tunnel_with_terminal():
    """Abre un tunel en la instancia seleccionada con parametros especificos."""
    try:
        selected_items = tree.selection()
        if not selected_items:
            messagebox.showerror("Wake Up!!!", "No instances selected")
            return

        instance_id = tree.item(selected_items[0])['values'][1]
        profile = selected_profile.get()

        if not profile:
            messagebox.showerror("Wake Up!!!", "Select profile")
            return

        localport = localport_entry.get()
        remoteport = remoteport_entry.get()

        if not localport or not remoteport:
            messagebox.showerror("Wake Up!!!", " Type LocalPort and RemotePort.")
            return

        command = (
            "aws ssm start-session --target "
            f"{instance_id} --profile {profile} "
            "--document-name AWS-StartPortForwardingSession "
            f"--parameters portNumber={remoteport},localPortNumber={localport}"
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

        threading.Thread(target=sendcommand, args=(instance_ids, command, selected_profile.get())).start()
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
            while invocation_result['Status'] == 'InProgress':
                invocation_result = ssm_client.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance
                )
            standard_output = invocation_result['StandardOutputContent']
            print(standard_output)

            output.append(f"Instance {instance}:\n{standard_output}\n{'='*120}\n")

        except Exception as e:
            print(e)
            messagebox.showerror("Wake Up!!!", str(e))

    command_output.configure(state="normal")
    command_output.delete("1.0", tk.END)
    command_output.insert(tk.END, output)
    command_output.configure(state="disabled")


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

    session = boto3.Session(profile_name=profile)
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
                    [{"expression": "(m1+m2)/(PERIOD(m1)-m3)", "label": "Average IOPS/s when volume is active", "id": "e1", "region": "eu-west-1"}],
                    [{"expression": "(m1+m2)/(PERIOD(m1))", "label": "Average IOPS/s", "id": "e2", "visible": False, "region": "eu-west-1"}],
                    [{"expression": "(m4+m5)/(PERIOD(m4)-m3)", "label": "Average Throughput when volume is active", "id": "e3", "region": "eu-west-1"}],
                    [{"expression": "(m4+m5)/(PERIOD(m4))", "label": "Average Throughput in bytes", "id": "e4", "visible": False, "region": "eu-west-1"}],
                    ["AWS/EBS", "VolumeReadOps", "VolumeId", volume["VolumeId"], {"region": "eu-west-1", "id": "m1", "visible": False}],
                    ["AWS/EBS", "VolumeWriteOps", "VolumeId", volume["VolumeId"], {"region": "eu-west-1", "id": "m2", "visible": False}],
                    ["AWS/EBS", "VolumeIdleTime", "VolumeId", volume["VolumeId"], {"region": "eu-west-1", "id": "m3", "visible": False}],
                    ["AWS/EBS", "VolumeReadBytes", "VolumeId", volume["VolumeId"], {"region": "eu-west-1", "id": "m4", "visible": False}],
                    ["AWS/EBS", "VolumeWriteBytes", "VolumeId", volume["VolumeId"], {"region": "eu-west-1", "id": "m5", "visible": False}]
                ],
                "view": "timeSeries",
                "stacked": False,
                "region": "eu-west-1",
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
    global localport_entry, remoteport_entry, command_input, command_output, analysis_output

    root = tk.Tk()
    root.title("AWS Connector @mndemnk")
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

    notebook = ttk.Notebook(root, style="TNotebook")
    notebook.pack(expand=True, fill="both")

    skin_path = get_skin_path()

    powercon_frame = create_tab_with_background(notebook, "PowerCon", skin_path)
    powertunnel_frame = create_tab_with_background(notebook, "PowerTunnel", skin_path)
    powercommand_frame = create_tab_with_background(notebook, "PowerCommand", skin_path)
    powerebs_frame = create_tab_with_background(notebook, "PowerEBS", skin_path)

    profile_frame = tk.Frame(powercon_frame, bg="black", padx=5, pady=5)
    profile_frame.place(x=20, y=20, relwidth=0.95)

    profile_menu_label = tk.Label(profile_frame, text="Search profile: ", font=("Consolas", 12), bg="black", fg="yellow")
    profile_menu_label.grid(row=0, column=0, padx=5, sticky="w")

    profile_search_entry = tk.Entry(profile_frame, font=("Consolas", 12), width=30, bg="white", fg="black", insertbackground="black")
    profile_search_entry.grid(row=0, column=1, padx=5, sticky="w")
    profile_search_entry.bind("<KeyRelease>", filter_profiles)

    profile_menu = ttk.Combobox(profile_frame, textvariable=selected_profile, font=("Consolas", 12), state="readonly", width=55)
    profile_menu.grid(row=0, column=2, padx=5, sticky="w")

    refresh_button = ttk.Button(profile_frame, text="Refresh Instances", command=refresh_instances, style="Action.TButton")
    refresh_button.grid(row=0, column=3, padx=5, sticky="e")

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

    banner_label = tk.Label(instance_frame, text="       * Welcome to AWSPowerConn Tool *              @Mndemnk          ", font=("Consolas", 12), bg="black", fg="yellow")
    banner_label.grid(row=1, column=3, padx=5, sticky="w")

    tree_frame = ttk.Frame(powercon_frame, style="TFrame")
    tree_frame.place(x=20, y=120, relwidth=0.95, relheight=0.7)

    tree = ttk.Treeview(tree_frame, columns=("Name", "InstanceID", "PrivateIP", "InstanceState"), show="headings", style="Treeview")
    tree.heading("Name", text="Name")
    tree.heading("InstanceID", text="Instance ID")
    tree.heading("PrivateIP", text="Private IP")
    tree.heading("InstanceState", text="State")
    tree.pack(expand=True, fill="both")

    tk.Label(powertunnel_frame, text="Local Port:", font=("Consolas", 13), bg="#1E1E1E", fg="yellow").pack(pady=7)
    localport_entry = tk.Entry(powertunnel_frame, font=("Consolas", 13), bg="white", fg="black", insertbackground="black")
    localport_entry.pack(pady=5)

    tk.Label(powertunnel_frame, text="Remote Port:", font=("Consolas", 13), bg="#1E1E1E", fg="yellow").pack(pady=7)
    remoteport_entry = tk.Entry(powertunnel_frame, font=("Consolas", 13), bg="white", fg="black", insertbackground="black")
    remoteport_entry.pack(pady=5)

    tk.Button(powertunnel_frame, text="Start Tunnel", command=open_tunnel_with_terminal, font=("Consolas", 13), bg="black", fg="white", activebackground="#222222", activeforeground="white").pack(pady=7)

    tk.Label(powercommand_frame, text="Command:", font=("Consolas", 13), bg="#1E1E1E", fg="yellow").pack(pady=6)
    command_input = tk.Text(powercommand_frame, height=7, width=145, font=("Consolas", 12), bg="black", fg="white")
    command_input.pack(pady=5)

    tk.Button(powercommand_frame, text="Send command", command=invocation, font=("Consolas", 13), bg="black", fg="white", activebackground="#222222", activeforeground="white").pack(pady=6)

    command_output = tk.Text(powercommand_frame, height=38, width=145, font=("Consolas", 12), bg="black", fg="#258EFE", state="disabled")
    command_output.pack(pady=5)

    tk.Label(powerebs_frame, text="EBS Data Analysis", font=("Consolas", 13), bg="#1E1E1E", fg="yellow").pack(pady=6)
    tk.Button(powerebs_frame, text="Create Dashboard", command=ebs_analysis, font=("Consolas", 12), bg="black", fg="white", activebackground="#222222", activeforeground="white").pack(pady=6)
    tk.Button(powerebs_frame, text="Select CSV file", command=seleccionar_archivo, font=("Consolas", 12), bg="black", fg="white", activebackground="#222222", activeforeground="white").pack(pady=6)
    analysis_output = tk.Text(powerebs_frame, height=40, width=120, font=("Consolas", 12), bg="black", fg="white", insertbackground="white")
    analysis_output.pack(padx=20, pady=10)

    root.mainloop()


if __name__ == "__main__":
    main()
