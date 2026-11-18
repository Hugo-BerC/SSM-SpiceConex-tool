"""EBS and baseline Performance page; independent from the application shell."""
from PySide6.QtWidgets import (QFrame,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QLineEdit,
    QPushButton,QComboBox,QPlainTextEdit,QMessageBox)
from pyside6_poc.base_page import BasePage
from pyside6_poc.dialogs import show_dialog,show_exception,_open_csv_dialog
from pyside6_poc.tasks import start_task,task_busy
from aws.operations import run_with_sso as _run_with_sso
from services.ebs import list_instance_volumes,create_cloudwatch_dashboard,suggested_dashboard_name
from services.performance import calculate_baseline

class PerformancePage(BasePage):
    def __init__(self, source):
        super().__init__(); self.source=source; root=self.page_header("EBS / PERFORMANCE ANALYSIS","Performance","Inspect attached EBS volumes, publish CloudWatch dashboards and calculate EBS baselines from CSV data.")
        grid=QGridLayout(); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(14)
        ebs=QFrame(); ebs.setObjectName("operationPanel"); ef=QVBoxLayout(ebs); ef.setContentsMargins(20,18,20,18); ef.setSpacing(10)
        self.ebs_target=QComboBox(); self.ebs_target.setMinimumHeight(42); ef.addWidget(self._label("INSTANCE")); ef.addWidget(self.ebs_target)
        buttons=QHBoxLayout(); self.load_ebs=QPushButton("LOAD EBS"); self.load_ebs.setObjectName("quietButton"); self.dashboard=QPushButton("CREATE CLOUDWATCH DASHBOARD"); self.dashboard.setObjectName("primaryButton"); buttons.addWidget(self.load_ebs); buttons.addWidget(self.dashboard); ef.addLayout(buttons)
        self.dashboard_name=QLineEdit(); self.dashboard_name.setPlaceholderText("Dashboard name is proposed after loading volumes"); self.dashboard_name.textChanged.connect(self.dashboard_name.setToolTip); ef.addWidget(self._label("DASHBOARD NAME")); ef.addWidget(self.dashboard_name)
        self.cancel=QPushButton("CANCEL READ / ANALYSIS"); self.cancel.setObjectName("quietButton"); ef.addWidget(self.cancel)
        self.ebs_output=QPlainTextEdit(); self.ebs_output.setReadOnly(True); self.ebs_output.setMinimumHeight(180); ef.addWidget(self.ebs_output); grid.addWidget(ebs,0,0)
        csv=QFrame(); csv.setObjectName("operationPanel"); cf=QVBoxLayout(csv); cf.setContentsMargins(20,18,20,18); cf.setSpacing(10); cf.addWidget(self._label("CSV BASELINE ANALYSIS")); self.csv_path=QLineEdit(); self.csv_path.setPlaceholderText("CSV with Timestamp, IOPS and Bytes/s columns"); cf.addWidget(self.csv_path); cr=QHBoxLayout(); self.browse=QPushButton("BROWSE"); self.browse.setObjectName("quietButton"); self.analyze=QPushButton("ANALYZE BASELINE"); self.analyze.setObjectName("primaryButton"); cr.addWidget(self.browse); cr.addWidget(self.analyze); cf.addLayout(cr); self.csv_output=QPlainTextEdit(); self.csv_output.setReadOnly(True); self.csv_output.setMinimumHeight(180); cf.addWidget(self.csv_output); grid.addWidget(csv,0,1)
        root.addLayout(grid); root.addStretch()
        self.thread=None; self._generation=0; self._volumes=[]; self._mode=""; self.cancel.clicked.connect(self.cancel_operation)
        source.instancesChanged.connect(self.set_instances); self.load_ebs.clicked.connect(self.load_ebs_data); self.dashboard.clicked.connect(self.create_dashboard); self.browse.clicked.connect(self.choose_csv); self.analyze.clicked.connect(self.analyze_csv); self.ebs_target.currentIndexChanged.connect(self._target_changed); self.set_instances(source.instances)
    def _label(self, text):
        label=QLabel(text); label.setObjectName("microLabel"); return label

    def _snapshot(self):
        target=self.ebs_target.currentData()
        return (self.aws_context.snapshot(), self._generation, target.instance_id if target else None)

    def _controls(self):
        busy=task_busy(self)
        self.ebs_target.setEnabled(not busy)
        self.load_ebs.setEnabled(not busy and self.ebs_target.currentData() is not None)
        self.dashboard.setEnabled(not busy and bool(self._volumes))
        for control in (self.browse,self.analyze,self.csv_path,self.dashboard_name): control.setEnabled(not busy)
        self.cancel.setEnabled(busy and self._mode != "publish")

    def set_instances(self, instances):
        self.ebs_target.blockSignals(True); self.ebs_target.clear()
        for instance in instances: self.ebs_target.addItem(f"{instance.name} · {instance.instance_id}",instance)
        self.ebs_target.blockSignals(False); self._target_changed()

    def context_changed(self):
        self.ebs_target.clear()
        self._target_changed()

    def _target_changed(self, *_args):
        self._generation+=1; self._volumes=[]; self.dashboard_name.clear()
        self.status.setText("◆  CONTEXT CHANGED — PREVIOUS OPERATION MAY FINISH" if task_busy(self) else "◆  READY")
        self.ebs_output.setPlainText("Select an instance and load its attached EBS volumes." if self.ebs_target.currentData() else
                                    "Load the EC2 fleet in Terminal to inspect EBS volumes.")
        self._controls()

    def _run(self, mode, operation, finished):
        if task_busy(self): return
        self._mode=mode
        start_task(self,operation,finished,self._failed,self._controls,context=self._snapshot)
        self._controls()

    def _failed(self, exc):
        self.status.setText("◆  PERFORMANCE ERROR")
        show_exception(self,"Performance",f"running Performance {self._mode}",exc)

    def cancel_operation(self):
        if self._mode == "publish": return
        self._generation+=1
        self.status.setText("◆  CANCELLED — WAITING FOR ACTIVE READ TO FINISH")
        self.cancel.setEnabled(False)

    def load_ebs_data(self):
        if task_busy(self): return
        if getattr(self.source,"demo",False):
            show_dialog(self,QMessageBox.Icon.Information,"Demo mode","EBS queries are unavailable in demo mode."); return
        instance=self.ebs_target.currentData()
        if not instance:
            show_dialog(self,QMessageBox.Icon.Warning,"No target selected","Select an EC2 instance first."); return
        profile=self.source.commands.profile.currentText().strip(); region=self.source.commands.region.currentText().strip()
        self._volumes=[]; self.status.setText("●  LOADING EBS")
        self._run("EBS query",lambda:_run_with_sso(profile,lambda:list_instance_volumes(profile,region,instance.instance_id)),self._volumes_loaded)

    def _volumes_loaded(self, volumes):
        self._volumes=volumes
        self.ebs_output.setPlainText("\n".join(f"{v['Device']}  {v['VolumeId']}  {v['Type']}  {v['Size']} GiB  IOPS={v['Iops']}  Throughput={v['Throughput']} MB/s" for v in volumes) or "No EBS volumes attached.")
        instance=self.ebs_target.currentData()
        if instance and volumes:
            self.dashboard_name.setText(suggested_dashboard_name(instance.name,self.source.commands.region.currentText().strip(),instance.instance_id))
            self.dashboard_name.setCursorPosition(0)
        self.status.setText("●  EBS LOADED")

    def create_dashboard(self):
        if task_busy(self): return
        instance=self.ebs_target.currentData(); volumes=[dict(volume) for volume in self._volumes]
        if not instance or not volumes:
            show_dialog(self,QMessageBox.Icon.Warning,"EBS data unavailable","Load attached EBS volumes first."); return
        if getattr(self.source,"demo",False):
            show_dialog(self,QMessageBox.Icon.Information,"Demo mode","Dashboard publishing is unavailable in demo mode."); return
        profile=self.source.commands.profile.currentText().strip(); region=self.source.commands.region.currentText().strip()
        name=self.dashboard_name.text().strip(); original=self._snapshot()
        if not name:
            show_dialog(self,QMessageBox.Icon.Warning,"Dashboard name","Enter the exact dashboard name before publishing."); return
        answer=QMessageBox.question(self,"Publish CloudWatch dashboard",
            f"Create this dashboard?\n\nProfile: {profile}\nRegion: {region}\nInstance: {instance.instance_id}\nDashboard: {name}\nVolumes: {', '.join(v['VolumeId'] for v in volumes)}\n\nExisting dashboards will be refused. CloudWatch dashboard charges may apply.",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes or original != self._snapshot(): return
        self.status.setText("●  PUBLISHING CONFIRMED DASHBOARD")
        self._run("publish",lambda:_run_with_sso(profile,lambda:create_cloudwatch_dashboard(
            profile,region,instance.name,volumes,dashboard_name=name,instance_id=instance.instance_id)),self._published)

    def _published(self, name):
        self.status.setText("●  DASHBOARD PUBLISHED")
        show_dialog(self,QMessageBox.Icon.Information,"CloudWatch dashboard created",f"Dashboard: {name}")

    def choose_csv(self):
        if task_busy(self): return
        path=_open_csv_dialog(self,"Select CSV")
        if path: self.csv_path.setText(path)

    def analyze_csv(self):
        if task_busy(self): return
        path=self.csv_path.text().strip()
        if not path: self.choose_csv(); path=self.csv_path.text().strip()
        if not path: return
        self.status.setText("●  ANALYZING BASELINE")
        self._run("baseline analysis",lambda:calculate_baseline(path),self._baseline_loaded)

    def _baseline_loaded(self, data):
        lines=[]
        for key,value in data.items():
            if isinstance(value,list): lines.append(key+":\n"+"\n".join(f"  {item}" for item in value))
            else: lines.append(f"{key}: {value:.2f}" if isinstance(value,float) else f"{key}: {value}")
        self.csv_output.setPlainText("\n".join(lines)); self.status.setText("●  BASELINE READY")
