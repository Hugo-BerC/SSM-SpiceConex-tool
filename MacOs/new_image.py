import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
import os

# Icono dummy
if not os.path.exists('icon_mock.png'):
    from PIL import ImageDraw
    img = Image.new('RGBA', (512, 512), (46, 46, 46, 255))  # Gris base
    draw = ImageDraw.Draw(img)
    draw.rectangle([80, 80, 432, 432], fill='#FF0000')
    draw.text((210, 260), '_>', fill='#FFFFFF')
    img.save('icon_mock.png')

root = tk.Tk()
root.title('SSM-PowerConnect - Mockup Moderno v2')
root.geometry('1200x800')
root.configure(bg='#1E1E1E')  # Gris oscuro main
root.iconphoto(False, ImageTk.PhotoImage(Image.open('icon_mock.png')))

# Estilos refinados: Grises + contraste alto
style = ttk.Style()
style.theme_use('clam')

# Colores grises modernos
style.configure('Dark.TFrame', background='#2D2D2D', relief='flat', borderwidth=1, bordercolor='#404040')
style.configure('Main.TFrame', background='#1E1E1E', relief='flat')
style.configure('Title.TLabel', background='#1E1E1E', foreground='#FFFFFF', font=('Segoe UI', 15, 'bold'))
style.configure('Sub.TLabel', background='#2D2D2D', foreground='#E0E0E0', font=('Segoe UI', 11))
style.configure('Search.TEntry', fieldbackground='#404040', foreground='#FFFFFF', 
                insertcolor='#FFD700', borderwidth=2, relief='solid', focuscolor='#FF0000')
style.map('Search.TEntry', bordercolor=[('focus', '#FFD700')], fieldbackground=[('focus', '#505050')])
style.configure('Connect.TButton', background='#FF0000', foreground='#FFFFFF', 
                focuscolor='#FFD700', font=('Segoe UI', 12, 'bold'), relief='flat', padding=10)
style.map('Connect.TButton', background=[('active', '#E50000'), ('pressed', '#CC0000')])
style.configure('Dropdown.TCombobox', fieldbackground='#404040', foreground='#FFFFFF', 
                arrowcolor='#FF0000', borderwidth=2, relief='solid')
style.map('Dropdown.TCombobox', fieldcolor=[('readonly', '#404040'), ('focus', '#505050')])
style.configure('Instance.Treeview', background='#2D2D2D', foreground='#E0E0E0', 
                fieldbackground='#2D2D2D', borderwidth=1, rowheight=38)
style.configure('Instance.Treeview.Heading', background='#FF0000', foreground='#FFFFFF', 
                font=('Segoe UI', 11, 'bold'), relief='flat', padding=8)
style.map('Instance.Treeview.Heading', background=[('active', '#E50000')])

# Data demo
profiles = ['Prod-EU', 'Dev-US', 'Staging-Madrid']
accounts = ['Cuenta AWS Prod (123456)', 'Cuenta AWS Dev (789012)', 'Cuenta Shared (345678)']
instances = [
    ('i-123abc', 'running', '54.123.45.67'),
    ('i-456def', 'stopped', '54.123.45.68'),
    ('i-789ghi', 'running', '54.123.45.69')
]

# Sidebar más gris claro
sidebar = ttk.Frame(root, style='Dark.TFrame', width=300)
sidebar.pack(side='left', fill='y', padx=(15, 10), pady=15)

ttk.Label(sidebar, text='AWS Profiles', style='Title.TLabel').pack(pady=(25, 15))
search_profile_var = tk.StringVar()
search_profile = ttk.Entry(sidebar, textvariable=search_profile_var, style='Search.TEntry', width=32)
search_profile.pack(pady=8, padx=20, fill='x')
search_profile.bind('<KeyRelease>', lambda e: print('🔍 Filtrando profiles...'))

dropdown_cuentas = ttk.Combobox(sidebar, values=accounts, state='readonly', style='Dropdown.TCombobox', width=30)
dropdown_cuentas.pack(pady=12, padx=20, fill='x')
dropdown_cuentas.set('Seleccionar Cuentas AWS...')

ttk.Separator(sidebar, orient='horizontal').pack(fill='x', padx=20, pady=(20, 30))

# Main más espacioso
main_frame = ttk.Frame(root, style='Main.TFrame')
main_frame.pack(side='left', fill='both', expand=True, padx=(10, 15), pady=15)

# Header search + button
header_frame = ttk.Frame(main_frame, style='Dark.TFrame')
header_frame.pack(fill='x', pady=(25, 15))

ttk.Label(header_frame, text='Search Instances', style='Title.TLabel').pack(anchor='w')
search_instance_var = tk.StringVar()
search_instance = ttk.Entry(header_frame, textvariable=search_instance_var, style='Search.TEntry', 
                           width=70, font=('Segoe UI', 12))
search_instance.pack(pady=15, padx=(0, 15), fill='x')
search_instance.insert(0, 'Filtrar por ID, tag:Name o región...')

btn_connect = ttk.Button(header_frame, text='🔌 Connect SSM', style='Connect.TButton')
btn_connect.pack(side='right', pady=15)
btn_connect.configure(command=lambda: messagebox.showinfo('SSM Connect', 
    '🚀 Conectando...\nssh aws ssm start-session --target i-123abc\nStatus: Online'))

# Treeview mejorado
tree_frame = ttk.Frame(main_frame, style='Dark.TFrame')
tree_frame.pack(fill='both', expand=True, pady=(0, 20))

columns = ('ID', 'State', 'IP')
tree = ttk.Treeview(tree_frame, columns=columns, show='headings', style='Instance.Treeview')
tree.heading('ID', text='Instance ID')
tree.heading('State', text='Status')
tree.heading('IP', text='Public IP')
tree.column('ID', width=280, anchor='w')
tree.column('State', width=120, anchor='center')
tree.column('IP', width=180, anchor='w')

scrollbar = ttk.Scrollbar(tree_frame, orient='vertical', command=tree.yview)
tree.configure(yscrollcommand=scrollbar.set)

tree.pack(side='left', fill='both', expand=True)
scrollbar.pack(side='right', fill='y')

# Datos con tags
for inst in instances:
    item = tree.insert('', 'end', values=inst)
    if inst[1] == 'running':
        tree.set(item, 'State', '🟢 Online')  # Icono emoji
        tree.item(item, tags=('online',))
tree.tag_configure('online', background='#3A3A3A', foreground='#FFD700')

# Status bar gris
status_frame = ttk.Frame(root, style='Dark.TFrame', height=30)
status_frame.pack(side='bottom', fill='x', pady=(0, 10))
status_frame.pack_propagate(False)
ttk.Label(status_frame, text='Ready | Multi-cuenta AWS SSM | Powered by CLI', 
          background='#2D2D2D', foreground='#B0B0B0', 
          font=('Segoe UI', 10)).pack(pady=5)

root.mainloop()
