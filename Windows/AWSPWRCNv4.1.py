import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import json
import time
import boto3
from botocore.exceptions import ClientError
from PIL import Image, ImageTk
from tkinter import filedialog
import numpy as np
import pandas as pd

def log_error(message):
    print(f"ERROR: {message}")

def run_command(command):
    try:
        print(f"Ejecutando comando: {command}")
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        log_error(f"Error ejecutando {command}: {e.stderr}")
        return None

def get_profiles():
    profiles = run_command("aws configure list-profiles").splitlines()
    return sorted(profiles)

def validate_session(profile):
    return run_command(f"aws sts get-caller-identity --profile {profile}") is not None

def login_sso(profile):
    run_command(f"wt -w 0 C:\windows\system32\wsl.exe -d Debian aws sso login --profile {profile}")
    return run_command(f"aws sso login --profile {profile}")

def get_instances(profile):
    try:
        # Crear la sesión con el perfil seleccionado
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

    instances = [] #Limpiar siempre antes de cargar
    
    try:
        instances = get_instances(profile)  # Obtenemos las instancias en tiempo real
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
    
    # Limpiamos el árbol de instancias y lo llenamos con los datos actuales
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
    """Inicia una sesión SSM en la instancia seleccionada."""
    try:
        if not instance_ids:
            messagebox.showerror("Wake Up!!!", "No instances selected.")
            return

        for instance_id in instance_ids:
            # Construir el comando para iniciar sesión SSM
            command = f"aws ssm start-session --target {instance_id} --profile {profile}"

            # Abrir una nueva pestaña en Windows Terminal y ejecutar el comando
            subprocess.run([
                "wt", 
                "-w",
                "0",
                "C:\windows\system32\wsl.exe",
                "-d",
                "Debian",  
                f"\"{command}\""
            ], shell=True)

    except Exception as e:
        # Registrar y mostrar cualquier error ocurrido
        log_error(f"Error al conectar a la instancia: {e}")
        messagebox.showerror("Wake Up!!!", f"Cannot connect to instance:\n{e}")

def open_tunnel_with_powershell():
    """Abre un túnel en la instancia seleccionada con parámetros específicos."""
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

        command = f"aws ssm start-session --target {instance_id} --profile {profile} --document-name AWS-StartPortForwardingSession --parameters portNumber={remoteport},localPortNumber={localport}"

        subprocess.run([
            "wt", 
            "-w",
            "0", 
            "new-tab", 
            "powershell", 
            "-NoExit", 
            "-Command", 
            f"\"{command}\""
        ], shell=True)

    except Exception as e:
        log_error(f"Error al abrir túnel: {e}")
        messagebox.showerror("Wake Up!!!", f"No se pudo abrir el túnel:\n{e}")


def invocation():
    try:
        ## SELECT INSTANCE IDS FROM CONSOLE
        selected_items = tree.selection()
        if not selected_items:
            raise ValueError("Debe seleccionar al menos una instancia.")

        instance_ids = [tree.item(item)['values'][1] for item in selected_items]
        command = command_input.get("1.0", tk.END).strip()
        if not command:
            raise ValueError("Debe ingresar un comando para ejecutar.")

        ## INVOKE SENDCOMMAND FUNCTION WITH PARAMETERS
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
            ## USING PROFILE SELECTED 
            session = boto3.Session(profile_name=profile)
            
            ## INITILICE SSM CLIENT THROUGH SESSION     
            ssm_client = session.client('ssm')
            
            response = ssm_client.send_command(
                InstanceIds=[instance],
                DocumentName="AWS-RunShellScript",
                Parameters={'commands': [command]}
            )

            ## CREATE COMMAND_ID
            command_id = response['Command']['CommandId']
            time.sleep(1)

            ## INVOKE COMMAND
            invocation_result = ssm_client.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance
                )    
            while invocation_result['Status']=='InProgress':
                invocation_result = ssm_client.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance
                    )
            ## APPEND OUTPUT INTO A LIST
            standard_output = invocation_result['StandardOutputContent']
            print(standard_output)
            
            output.append(f"Instance {instance}:\n{standard_output}\n{'='*120}\n")
            
        except Exception as e:
            print(e)
            messagebox.showerror("Wake Up!!!", str(e))

    ## DISPLAY OUTPUT ON SCREEN

    command_output.configure(state="normal")
    command_output.delete("1.0", tk.END)
    command_output.insert(tk.END, output)
    command_output.configure(state="disabled")

# Función para añadir imagen de fondo en las pestañas
def add_tab_with_background(notebook, text, image_path):
    frame = ttk.Frame(notebook, style="TFrame")
    notebook.add(frame, text=text)

    canvas = tk.Canvas(frame, highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    # Cargar la imagen de fondo
    bg_image = Image.open(image_path)
    bg_photo = ImageTk.PhotoImage(bg_image)
    canvas.bg_photo = bg_photo  # Referencia para evitar recolección de basura

    # Dibujar la imagen de fondo
    bg_id = canvas.create_image(0, 0, anchor="nw", image=bg_photo)

    # Actualizar tamaño del canvas y redibujar fondo al redimensionar
    def resize_background(event):
        canvas.coords(bg_id, 0, 0)
        canvas.itemconfig(bg_id, image=canvas.bg_photo)

    canvas.bind("<Configure>", resize_background)

    return frame, canvas


def create_tab_with_background(notebook, text, image_path):
    frame = ttk.Frame(notebook, style="TFrame")
    notebook.add(frame, text=text)

    # Cargar la imagen de fondo
    bg_image = Image.open(image_path)
    bg_photo = ImageTk.PhotoImage(bg_image.resize((1920, 1080)))  # Redimensionar la imagen al tamaño de la ventana

    # Añadir fondo como etiqueta
    background_label = tk.Label(frame, image=bg_photo)
    background_label.image = bg_photo  # Evitar recolección de basura
    background_label.place(relwidth=1, relheight=1)  # Expandir al tamaño del frame

    return frame


def calcular_lineas_base(archivo_csv):
    try:
        # Leer el archivo CSV (delimitado por comas)
        df = pd.read_csv(archivo_csv)

        # Verificar que el archivo tiene al menos tres columnas (timestamp, IOPS y throughput)
        if df.shape[1] < 3:
            raise ValueError("El archivo debe tener al menos tres columnas: Timestamp, IOPS por minuto y Bytes por segundo.")
        
        # Convertir la columna de fecha y hora a tipo datetime para análisis temporal
        df['timestamp'] = pd.to_datetime(df.iloc[:, 0])  # Suponemos que la primera columna es Timestamp
        
        # Suponemos que la segunda columna es IOPS por minuto y la tercera es Bytes por segundo
        iops_col = df.iloc[:, 1]  
        bytes_seg_col = df.iloc[:, 2]  

        iops_seg = iops_col
        throughput_mb = bytes_seg_col / 1_048_576  # Conversión de Bytes a MB

        # Cálculos de las métricas de línea base
        throughput_min = throughput_mb.min()  # Línea base mínima de throughput
        throughput_max = throughput_mb.max()  # Línea base máxima de throughput
        throughput_prom = throughput_mb.mean()  # Promedio de throughput
        throughput_std = throughput_mb.std()  # Desviación estándar de throughput

        iops_min = iops_seg.min()  # Línea base mínima de IOPS/s
        iops_max = iops_seg.max()  # Línea base máxima de IOPS/s
        iops_prom = iops_seg.mean()  # Promedio de IOPS/s
        iops_std = iops_seg.std()  # Desviación estándar de IOPS/s

        # Identificar outliers (valores fuera de 2 desviaciones estándar)
        throughput_outliers = throughput_mb[(throughput_mb < throughput_prom - 2 * throughput_std) | 
                                            (throughput_mb > throughput_prom + 2 * throughput_std)]
        iops_outliers = iops_seg[(iops_seg < iops_prom - 2 * iops_std) | 
                                 (iops_seg > iops_prom + 2 * iops_std)]

        # Agregar análisis de patrones por hora y día
        df['hour'] = df['timestamp'].dt.hour
        df['day_of_week'] = df['timestamp'].dt.dayofweek

        # Filtrar los valores extremos por percentil 95 (valor máximo que no sea un pico anómalo)
        throughput_percentil_95 = np.percentile(throughput_mb, 95)
        iops_percentil_95 = np.percentile(iops_seg, 95)

        # Filtrar los valores que están por encima del percentil 95
        df_filtered = df[(throughput_mb <= throughput_percentil_95) & (iops_seg <= iops_percentil_95)]

        # Cálculo del throughput máximo por hora (usando percentil 95 para eliminar picos extremos)
        throughput_by_hour_max = df_filtered.groupby('hour')[throughput_mb.name].max()
        iops_by_hour_max = df_filtered.groupby('hour')[iops_seg.name].max()

        # Cálculo del throughput máximo por día de la semana (usando percentil 95 para eliminar picos extremos)
        throughput_by_day_max = df_filtered.groupby('day_of_week')[throughput_mb.name].max()
        iops_by_day_max = df_filtered.groupby('day_of_week')[iops_seg.name].max()

        # Recomendación de aprovisionamiento de EBS usando percentiles
        recommended_throughput_95 = np.percentile(throughput_mb, 95)  # Usamos el percentil 95 para aprovisionamiento
        recommended_iops_95 = np.percentile(iops_seg, 95)  # Usamos el percentil 95 para aprovisionamiento

        # Recomendación para microbursting (falso techo) usando percentil 99
        recommended_throughput_99 = np.percentile(throughput_mb, 99)  # Usamos el percentil 99 para picos extremos
        recommended_iops_99 = np.percentile(iops_seg, 99)  # Usamos el percentil 99 para picos extremos

        # Opción de "techo" adicional para microbursting: podemos tomar el máximo de los valores
        # y sumarle un margen adicional (ej. 10% extra para cubrir picos inesperados)
        recommended_throughput_max = throughput_mb.max() * 1.1  # 10% más sobre el máximo
        recommended_iops_max = iops_seg.max() * 1.1  # 10% más sobre el máximo

        # **Detección de Microbursting**:
        microbursts_throughput = []
        microbursts_iops = []
        
        # Detectar microbursts de throughput (valores superiores al percentil 99)
        in_microburst = False
        microburst_start = None
        for idx, value in enumerate(throughput_mb):
            if value > recommended_throughput_99:
                if not in_microburst:
                    microburst_start = df['timestamp'][idx]  # Marca el inicio del microbursting
                    in_microburst = True
            else:
                if in_microburst:
                    microburst_end = df['timestamp'][idx]  # Marca el final del microbursting
                    duration = (microburst_end - microburst_start).total_seconds()  # Duración en segundos
                    microbursts_throughput.append((microburst_start, microburst_end, duration))
                    in_microburst = False
        
        # Detectar microbursts de IOPS (valores superiores al percentil 99)
        in_microburst = False
        for idx, value in enumerate(iops_seg):
            if value > recommended_iops_99:
                if not in_microburst:
                    microburst_start = df['timestamp'][idx]  # Marca el inicio del microbursting
                    in_microburst = True
            else:
                if in_microburst:
                    microburst_end = df['timestamp'][idx]  # Marca el final del microbursting
                    duration = (microburst_end - microburst_start).total_seconds()  # Duración en segundos
                    microbursts_iops.append((microburst_start, microburst_end, duration))
                    in_microburst = False

        # Resultados
        resultados = {
            'Línea Base Mínima de Throughput (MB/s)': throughput_min,
            'Línea Base Máxima de Throughput (MB/s)': throughput_max,
            'Línea Base Promedio de Throughput (MB/s)': throughput_prom,
            'Línea Base Mínima de IOPS/s': iops_min,
            'Línea Base Máxima de IOPS/s': iops_max,
            'Línea Base Promedio de IOPS/s': iops_prom,
            'Recomendación de Throughput para EBS (MB/s)': recommended_throughput_95,
            'Recomendación de IOPS para EBS': recommended_iops_95,
            'Recomendación de Throughput para EBS (Microbursting) (MB/s)': recommended_throughput_99,
            'Recomendación de IOPS para EBS (Microbursting)': recommended_iops_99,
            'Recomendación de Throughput para EBS (Techo Máximo) (MB/s)': recommended_throughput_max,
            'Recomendación de IOPS para EBS (Techo Máximo)': recommended_iops_max, 
            'Microbursts de Throughput (Momento de Inicio, Momento de Fin, Duración en Segundos)': microbursts_throughput,
            'Microbursts de IOPS (Momento de Inicio, Momento de Fin, Duración en Segundos)': microbursts_iops
        }

        return resultados
    
    except Exception as e:
        messagebox.showerror("Error", f"Hubo un problema al procesar el archivo: {str(e)}")


def seleccionar_archivo():
    # Abrir el diálogo para seleccionar un archivo CSV
    archivo = filedialog.askopenfilename(
        title="Seleccionar archivo CSV",
        filetypes=(("Archivos CSV", "*.csv"), ("Todos los archivos", "*.*"))
    )
    if archivo:
        # Procesar el archivo seleccionado
        resultados = calcular_lineas_base(archivo)

        if resultados:
            analysis_output.configure(state="normal")
            # Limpiar el área de texto antes de mostrar los nuevos resultados
            analysis_output.delete(1.0, tk.END)
            
            # Mostrar los resultados en la interfaz
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
                    analysis_output.insert(tk.END, f"{key}: {value:.2f}\n" if isinstance(value, float) else f"{key}: {value}\n")
            analysis_output.insert(tk.END, "\n")


def ebs_analysis():
    # Get instance name
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

    # Configuramos la sesión de AWS
    session = boto3.Session(profile_name=profile)
    ec2_client = session.client('ec2')
    cloudwatch_client = session.client('cloudwatch')
    
    # Obtenemos la lista de volúmenes asociados a la instancia
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

    # Crear el archivo JSON para el dashboard
    dashboard_body = {
        "widgets": []
    }

    for volume in volume_list:
        widget = {
            "type": "metric",
            "properties": {
                "metrics": [
                    [{ "expression": "(m1+m2)/(PERIOD(m1)-m3)", "label": "Average IOPS/s when volume is active", "id": "e1", "region": "eu-west-1" }],
                    [{ "expression": "(m1+m2)/(PERIOD(m1))", "label": "Average IOPS/s", "id": "e2", "visible": False, "region": "eu-west-1" }],
                    [{ "expression": "(m4+m5)/(PERIOD(m4)-m3)", "label": "Average Throughput when volume is active", "id": "e3", "region": "eu-west-1" }],
                    [{ "expression": "(m4+m5)/(PERIOD(m4))", "label": "Average Throughput in bytes", "id": "e4", "visible": False, "region": "eu-west-1" }],
                    ["AWS/EBS", "VolumeReadOps", "VolumeId", volume["VolumeId"], { "region": "eu-west-1", "id": "m1", "visible": False }],
                    ["AWS/EBS", "VolumeWriteOps", "VolumeId", volume["VolumeId"], { "region": "eu-west-1", "id": "m2", "visible": False }],
                    ["AWS/EBS", "VolumeIdleTime", "VolumeId", volume["VolumeId"], { "region": "eu-west-1", "id": "m3", "visible": False }],
                    ["AWS/EBS", "VolumeReadBytes", "VolumeId", volume["VolumeId"], { "region": "eu-west-1", "id": "m4", "visible": False }],
                    ["AWS/EBS", "VolumeWriteBytes", "VolumeId", volume["VolumeId"], { "region": "eu-west-1", "id": "m5", "visible": False }]
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

    # Publicar el dashboard en CloudWatch
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
        ebs_iops = ebs_info["Volumes"][0].get("Iops", "N/A")  # IOPS aprovisionados
        ebs_throughput = ebs_info["Volumes"][0].get("Throughput", "N/A")  # Throughput aprovisionado
        
        ebs_performance.append(
            f"Volume {ebs_device}: {volume_id}, IOPS: {ebs_iops}, Throughput: {ebs_throughput} MB/s"
        )
        
    analysis_output.configure(state="normal")
    # Limpiar el área de texto antes de mostrar los nuevos resultados
    analysis_output.delete(1.0, tk.END)
    analysis_output.insert(tk.END, "\n".join(ebs_performance))
    analysis_output.configure(state="disabled")


    # Volcar los datos en un archivo de texto
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
    style.configure("TFrame", background="#black")
    
    selected_profile = tk.StringVar()

    # Crear pestañas
    notebook = ttk.Notebook(root, style="TNotebook")
    notebook.pack(expand=True, fill="both")

    # Crear las pestañas con fondo
    powercon_frame = create_tab_with_background(notebook, "PowerCon", "skin.jpg")
    powertunnel_frame = create_tab_with_background(notebook, "PowerTunnel", "skin.jpg")
    powercommand_frame = create_tab_with_background(notebook, "PowerCommand", "skin.jpg")
    powerebs_frame = create_tab_with_background(notebook, "PowerEBS", "skin.jpg")

    # --- PowerCon Tab ---
    profile_frame = ttk.Frame(powercon_frame, style="TFrame", padding=5)
    profile_frame.place(x=20, y=20, relwidth=0.95)

    profile_menu_label = tk.Label(profile_frame, text="Search profile: ", font=("Consolas", 12), bg="black", fg="yellow")
    profile_menu_label.grid(row=0, column=0, padx=5, sticky="w")


    # Cuadro de búsqueda de perfil
    profile_search_entry = tk.Entry(profile_frame, font=("Consolas", 12), width=30, bg="#2E2E2E", fg="#258EFE")
    profile_search_entry.grid(row=0, column=1, padx=5, sticky="w")  # Columna 1, a la derecha de "Seleccionar Perfil"
    profile_search_entry.bind("<KeyRelease>", filter_profiles)

    profile_menu = ttk.Combobox(profile_frame, textvariable=selected_profile, font=("Consolas", 12), state="readonly", width=55)
    profile_menu.grid(row=0, column=2, padx=5, sticky="w")

    refresh_button = tk.Button(profile_frame, text="Refresh Instances", command=refresh_instances, font=("Consolas", 12), bg="black", fg="white")
    refresh_button.grid(row=0, column=3, padx=5, sticky="e")

    # Obtener y cargar perfiles en el Combobox
    profiles = get_profiles()
    if profiles:
        profile_menu["values"] = profiles
        profile_menu.set(profiles[0])  # Seleccionar el primer perfil por defecto
    else:
        messagebox.showerror("Wake Up!!!", "No se encontraron perfiles de AWS configurados.")

    # Cuadro de búsqueda de instancias
    instance_frame = ttk.Frame(powercon_frame, style="TFrame")
    instance_frame.place(x=20, y=60, relwidth=0.95)

    instance_search_label = tk.Label(instance_frame, text="Search instances:", font=("Consolas", 12), bg="black", fg="yellow")
    instance_search_label.grid(row=1, column=0, padx=5, sticky="w")

    instance_search_entry = tk.Entry(instance_frame, font=("Consolas", 12), width=30, bg="#2E2E2E", fg="#258EFE")
    instance_search_entry.grid(row=1, column=1, padx=5, sticky="w")
    instance_search_entry.bind("<KeyRelease>", filter_instances)

    connect_button = tk.Button(instance_frame, text="Connection", command=connect_selected_instances, font=("Consolas", 12), bg="black", fg="white")
    connect_button.grid(row=1, column=2, padx=5, sticky="e")

    banner_label = tk.Label(instance_frame, text="       * Welcome to AWSPowerConn Tool *              @Mndemnk          ", font=("Consolas", 12), bg="black", fg="yellow")
    banner_label.grid(row=1, column=3, padx=5, sticky="w")

    """
    inventory_buttom = tk.Button(instance_frame, text="Access data", command=load_data_button, font=("Consolas", 12), bg="black", fg="white")
    inventory_buttom.grid(row=1, column=7, padx=5, sticky="e")
    """

    # Crear Treeview para mostrar instancias
    tree_frame = ttk.Frame(powercon_frame, style="TFrame")
    tree_frame.place(x=20, y=120, relwidth=0.95, relheight=0.7)

    tree = ttk.Treeview(tree_frame, columns=("Name", "InstanceID", "PrivateIP", "InstanceState"), show="headings", style="Treeview")
    tree.heading("Name", text="Name")
    tree.heading("InstanceID", text="Instance ID")
    tree.heading("PrivateIP", text="Private IP")
    tree.heading("InstanceState", text="State")
    tree.pack(expand=True, fill="both")

    # --- PowerTunnel Tab ---
    tk.Label(powertunnel_frame, text="Local Port:", font=("Consolas", 13), bg="#2E2E2E", fg="yellow").pack(pady=7)
    localport_entry = tk.Entry(powertunnel_frame, font=("Consolas", 13), bg="#2E2E2E", fg="white")
    localport_entry.pack(pady=5)

    tk.Label(powertunnel_frame, text="Remote Port:", font=("Consolas", 13), bg="#2E2E2E", fg="yellow").pack(pady=7)
    remoteport_entry = tk.Entry(powertunnel_frame, font=("Consolas", 13), bg="#2E2E2E", fg="white")
    remoteport_entry.pack(pady=5)

    tk.Button(powertunnel_frame, text="Start Tunnel", command=open_tunnel_with_powershell, font=("Consolas", 13), bg="#444444", fg="yellow").pack(pady=7)

    # --- PowerCommand Tab ---
    tk.Label(powercommand_frame, text="Command:", font=("Consolas", 13), bg="#2E2E2E", fg="yellow").pack(pady=6)
    command_input = tk.Text(powercommand_frame, height=7, width=145, font=("Consolas", 12), bg="black", fg="white")
    command_input.pack(pady=5)

    tk.Button(powercommand_frame, text="Send command", command=invocation, font=("Consolas", 13), bg="#444444", fg="yellow").pack(pady=6)

    command_output = tk.Text(powercommand_frame, height=38, width=145, font=("Consolas", 12), bg="black", fg="#258EFE", state="disabled")
    command_output.pack(pady=5)


    # COLORS: #1CED1C
    
    # --- PowerEBS Tab ---
    tk.Label(powerebs_frame, text="EBS Data Analysis", font=("Consolas", 13), bg="#2E2E2E", fg="yellow").pack(pady=6)
    tk.Button(powerebs_frame, text="Create Dashboard", command=ebs_analysis, font=("Consolas", 12), bg="#444444", fg="yellow").pack(pady=6)
    tk.Button(powerebs_frame, text="Select CSV file", command=seleccionar_archivo, font=("Consolas", 12), bg="#444444", fg="yellow").pack(pady=6)
    analysis_output = tk.Text(powerebs_frame, height=40, width=120, font=("Consolas", 12), bg="black", fg="white", insertbackground="white")
    analysis_output.pack(padx=20, pady=10)
    

    root.mainloop()

if __name__ == "__main__":
    main()


