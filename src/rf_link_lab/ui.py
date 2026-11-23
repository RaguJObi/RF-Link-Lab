"""Native desktop workbench. No hardware is accessed on startup."""
from __future__ import annotations

import dataclasses
import html
import json
import math
import os
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QRectF, QPointF, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QGridLayout, QLabel, QPushButton, QFrame, QStackedWidget, QDoubleSpinBox, QSpinBox,
    QCheckBox, QComboBox, QTextBrowser, QFileDialog, QMessageBox, QScrollArea)

from .simulation import SimulationConfig, run_simulation
from .storage import save_run, load_run
from .paths import resource_root,state_root
from . import __version__

ROOT = state_root()
_FONTS_READY = False
BG, PANEL, EDGE, TEXT, MUTED, GREEN, BLUE = "#0b1320", "#111f30", "#23364a", "#e7eff8", "#91a7bf", "#51d7ba", "#6faeff"

STYLE = f"""
QMainWindow,QWidget{{background:{BG};color:{TEXT};font-family:'Segoe UI';font-size:13px;}}
QFrame#sidebar{{background:#0e1928;border-right:1px solid {EDGE};}}
QFrame#panel{{background:{PANEL};border:1px solid {EDGE};border-radius:10px;}}
QFrame#panel QLabel{{background:transparent;}}
QLabel#eyebrow{{color:{GREEN};font-size:11px;font-weight:700;letter-spacing:2px;}}
QLabel#title{{font-size:27px;font-weight:700;}}
QLabel#muted{{color:{MUTED};}}
QLabel#metric{{font-size:25px;font-weight:700;}}
QPushButton{{background:#192d42;border:1px solid #2c455e;border-radius:6px;padding:10px 14px;font-weight:600;}}
QPushButton:hover{{background:#233e57;}} QPushButton:disabled{{color:#62758a;background:#152333;}}
QPushButton#primary{{background:{GREEN};color:#081c1b;border:0;}}
QPushButton#primary:disabled{{background:#152333;color:#62758a;border:1px solid #2c455e;}}
QPushButton#nav{{text-align:left;background:transparent;border:0;padding:13px 12px;color:{MUTED};}}
QPushButton#nav:checked{{color:{TEXT};background:#1a3044;border-left:3px solid {GREEN};border-radius:3px;}}
QSpinBox,QDoubleSpinBox,QComboBox{{background:#0b1624;border:1px solid #324b64;border-radius:5px;padding:6px;color:{TEXT};min-height:20px;}}
QTextBrowser{{background:{PANEL};border:1px solid {EDGE};border-radius:8px;padding:12px;selection-background-color:#28506b;}}
QScrollArea{{border:0;}} QCheckBox{{spacing:7px;}} QCheckBox::indicator{{width:16px;height:16px;}}
QToolTip{{background:#1e344b;color:{TEXT};border:1px solid {EDGE};}}
"""


def label(text, kind=None, wrap=True):
    w = QLabel(text)
    if kind:
        w.setObjectName(kind)
    w.setWordWrap(wrap)
    return w


def panel():
    w = QFrame()
    w.setObjectName("panel")
    layout = QVBoxLayout(w)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    return w, layout


class Plot(QWidget):
    def __init__(self, title, scatter=False):
        super().__init__()
        self.title, self.scatter = title, scatter
        self.x, self.y = np.array([]), np.array([])
        self.setMinimumSize(280, 245)

    def set_data(self, x, y):
        self.x, self.y = np.asarray(x, float), np.asarray(y, float)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(PANEL))
        p.setPen(QColor(TEXT))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        p.drawText(16, 25, self.title)
        box = QRectF(55, 45, self.width()-76, self.height()-97)
        if box.width() <= 0 or box.height() <= 0:
            return
        if self.scatter:
            xmin,xmax,ymin,ymax = -1.6,1.6,-1.6,1.6
        else:
            xmin,xmax = (-100,100) if not self.x.size else (float(np.nanmin(self.x)),float(np.nanmax(self.x)))
            ymin,ymax = (-100., 0.) if not self.y.size else (min(-90.,float(np.nanmin(self.y))-3),max(0.,float(np.nanmax(self.y))+3))
        p.setFont(QFont("Segoe UI", 8))
        for i in range(5):
            xx,yy = box.left()+box.width()*i/4,box.top()+box.height()*i/4
            p.setPen(QColor(EDGE))
            p.drawLine(QPointF(xx,box.top()),QPointF(xx,box.bottom()))
            p.drawLine(QPointF(box.left(),yy),QPointF(box.right(),yy))
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(xx-28,box.bottom()+7,56,16),Qt.AlignmentFlag.AlignCenter,f"{xmin+(xmax-xmin)*i/4:.1f}")
            p.drawText(QRectF(0,yy-8,47,16),Qt.AlignmentFlag.AlignRight,f"{ymax-(ymax-ymin)*i/4:.1f}")
        p.drawText(QRectF(50,self.height()-24,self.width()-70,18),Qt.AlignmentFlag.AlignCenter,"In-phase (normalized)" if self.scatter else "Baseband frequency offset (kHz)")
        p.save()
        p.translate(13,box.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-100,-9,200,18),Qt.AlignmentFlag.AlignCenter,"Quadrature (normalized)" if self.scatter else "Bin power (dB re unit power)")
        p.restore()
        if not self.x.size:
            return
        good=np.isfinite(self.x)&np.isfinite(self.y)
        x,y=self.x[good],self.y[good]
        stride=max(1,len(x)//1800)
        p.save()
        p.setClipRect(box)
        p.setPen(QPen(QColor(GREEN),1.3))
        points=[QPointF(box.left()+(a-xmin)/max(xmax-xmin,1e-9)*box.width(),box.bottom()-(b-ymin)/max(ymax-ymin,1e-9)*box.height()) for a,b in zip(x[::stride],y[::stride])]
        if self.scatter:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(81,215,186,150))
            for point in points:
                p.drawEllipse(point,2,2)
        elif points:
            path=QPainterPath(points[0])
            for point in points[1:]: path.lineTo(point)
            p.drawPath(path)
        p.restore()


class Worker(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.result.emit(self.fn())
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


LESSONS = [
    ("01 · Telemetry", "Start with a message you can verify.",
     "Imagine validating a remote sensor before a software release. The STM32 sends its sequence number, uptime and button state. A CRC checks whether the bytes changed. Sequence numbers reveal missing packets.",
     "Predict: a CRC failure and a missing sequence number describe different observations. Which would you investigate first?",
     "A CRC failure means a received frame is damaged. A sequence gap means an expected frame was not delivered; inspect framing, acquisition and transport evidence before blaming RF."),
    ("02 · I/Q waveform", "Numbers become a radio waveform.",
     "QPSK maps each two-bit group to one of four complex points. I is the horizontal component; Q is the vertical component. Pulse shaping turns abrupt symbols into a controlled-bandwidth waveform.",
     "At 200,000 samples/s and four samples/symbol, calculate symbols/s and raw QPSK bit rate.",
     "200,000 ÷ 4 = 50,000 symbols/s. Two bits/symbol gives 100,000 bit/s before preamble, framing and other overhead. This is not application throughput."),
    ("03 · Channel", "Change one condition, then measure.",
     "Noise scatters points. Frequency error rotates phase over time. Excessive signal level clips the sampled waveform. These effects can produce similar packet failures, so one failure count cannot locate a cause.",
     "Predict what happens when you raise signal level beyond the ADC rails. Does more gain always help?",
     "No. Clipping distorts the waveform. Lower gain in a controlled repeat and compare clipping fraction, EVM and CRC results. This is a proposed diagnostic experiment, not proof of a failing component."),
    ("04 · Receiver", "Recover the message from observations.",
     "A known preamble helps find the burst and estimate frequency error. Matched filtering and symbol sampling recover points; demapping reconstructs bytes. CRC checks them independently.",
     "Can zero observed bit errors coexist with nonzero EVM?",
     "Yes. Symbols can move away from their ideal reference while staying inside the correct decision region. Report the number of compared bits; zero errors in a short test is not proof of zero long-term BER."),
    ("05 · AI experiment", "A diagnosis should lead to a test.",
     "The model sees measured features: estimated offset, EVM, clipping and noise statistics. It does not receive the injected fault label or channel controls. Its recommendation changes one simulated condition so you can compare before and after.",
     "Why must all windows from one recording session stay in the same dataset split?",
     "Adjacent windows share acquisition conditions. Splitting them randomly can make a model memorize a session and exaggerate generalization. Hold out whole sessions, compare with rules and evaluate on real hardware separately.")
]


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        # The offscreen Qt platform may not enumerate Windows system fonts.
        # Register the same real fonts explicitly for both preview and desktop.
        global _FONTS_READY
        if not _FONTS_READY:
            fonts=Path(os.environ.get("WINDIR",r"C:\Windows"))/"Fonts"
            for name in ["segoeui.ttf","segoeuib.ttf","seguisym.ttf"]:
                if (fonts/name).is_file():QFontDatabase.addApplicationFont(str(fonts/name))
            for name in ["DejaVuSans.ttf","DejaVuSans-Bold.ttf"]:
                path=Path("/usr/share/fonts/truetype/dejavu")/name
                if path.is_file():QFontDatabase.addApplicationFont(str(path))
            _FONTS_READY=True
        ROOT.mkdir(parents=True,exist_ok=True)
        self.setWindowTitle(f"RF Link Lab v{__version__} | Telemetry & AI diagnostics")
        self.resize(1440, 940)
        self.setMinimumSize(1100,760)
        self.result = None
        self.diagnosis = None
        self.model = None
        self.evaluation = None
        self._worker = None
        self._before = None
        self.setStyleSheet(STYLE)
        base=QWidget(); self.setCentralWidget(base)
        outer=QHBoxLayout(base);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        side=QFrame();side.setObjectName("sidebar");side.setFixedWidth(215)
        sl=QVBoxLayout(side);sl.setContentsMargins(19,28,19,24);sl.setSpacing(10)
        sl.addWidget(label("RF LINK LAB","eyebrow"));sl.addWidget(label("Inspect. Explain.\nExperiment.","title"));sl.addSpacing(26)
        self.pages=QStackedWidget();self.nav=[]
        for idx,name in enumerate(["Understand the link","Signal workbench","AI diagnosis","Saved evidence","Hardware day"]):
            b=QPushButton(name);b.setObjectName("nav");b.setCheckable(True);b.clicked.connect(lambda checked=False,i=idx:self.select_page(i));sl.addWidget(b);self.nav.append(b)
        sl.addStretch();sl.addWidget(label(f"VERSION {__version__}\n\nSimulation + serial\nIndependent I/Q replay\nBench session logging","muted"))
        outer.addWidget(side)
        area=QWidget();al=QVBoxLayout(area);al.setContentsMargins(28,22,28,18);al.setSpacing(16)
        self.status=label("LOCAL WORKBENCH  /  SIMULATION ONLY","eyebrow")
        al.addWidget(self.status);al.addWidget(self.pages,1)
        self.footer=label("A learning and validation tool. Simulated measurements are not hardware performance evidence.","muted")
        al.addWidget(self.footer);outer.addWidget(area,1)
        self.build_learn();self.build_workbench();self.build_ai();self.build_evidence();self.build_hardware()
        self.select_page(0);self.load_default_model()
        QTimer.singleShot(80,self.run_current)

    def add_page(self):
        content=QWidget();layout=QVBoxLayout(content);layout.setContentsMargins(0,0,6,0);layout.setSpacing(15)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(content);self.pages.addWidget(scroll)
        return layout

    def select_page(self,index):
        self.pages.setCurrentIndex(index)
        for i,b in enumerate(self.nav):b.setChecked(i==index)
        if index==4:self._link_activity(self.hardware_panel.conducted_panel.is_busy())

    def _link_activity(self,active):
        panel=self.hardware_panel.conducted_panel
        if active:self.status.setText('FINITE LINK EXPERIMENT  /  '+str(panel._mode).upper()+'  /  WAIT FOR CLEANUP')
        elif panel.last_result:self.status.setText('FINITE LINK  /  '+panel.last_result['mode'].upper()+'  /  '+panel.last_result['status'].upper())
        else:self.status.setText('HARDWARE BENCH  /  EXPLICIT START AND STOP CONTROLS')

    def build_learn(self):
        lay=self.add_page();lay.addWidget(label("Understand the link before you diagnose it.","title"))
        lay.addWidget(label("A remote telemetry message must arrive intact. Follow its path, then introduce one fault and inspect the evidence.","muted"))
        steps=QHBoxLayout()
        self.lesson_buttons=[]
        for i,lesson in enumerate(LESSONS):
            b=QPushButton(lesson[0]);b.clicked.connect(lambda checked=False,j=i:self.show_lesson(j));steps.addWidget(b);self.lesson_buttons.append(b)
        lay.addLayout(steps)
        w,pl=panel();self.lesson_title=label("","title");self.lesson_body=label("");self.lesson_question=label("");self.lesson_answer=label("","muted")
        pl.addWidget(self.lesson_title);pl.addWidget(self.lesson_body);pl.addSpacing(10);pl.addWidget(label("TRY EXPLAINING THIS","eyebrow"));pl.addWidget(self.lesson_question)
        self.reveal=QPushButton("Show explanation");self.reveal.clicked.connect(lambda:self.lesson_answer.setVisible(True));pl.addWidget(self.reveal);pl.addWidget(self.lesson_answer);lay.addWidget(w)
        w,pl=panel();pl.addWidget(label("The experiment you will build","eyebrow"));pl.addWidget(label("STM32 telemetry  →  QPSK waveform  →  channel  →  receiver  →  verified bytes"));pl.addWidget(label("Today the STM32 and channel are simulated. The receiver still works from sampled I/Q and checks decoded frames. Later, real serial telemetry and bladeRF captures replace specific boundaries.","muted"));lay.addWidget(w)
        b=QPushButton("Open the signal workbench");b.setObjectName("primary");b.clicked.connect(lambda:self.select_page(1));lay.addWidget(b);lay.addStretch();self.show_lesson(0)

    def show_lesson(self,index):
        _,title,body,question,answer=LESSONS[index]
        self.lesson_title.setText(title);self.lesson_body.setText(body);self.lesson_question.setText(question);self.lesson_answer.setText(answer);self.lesson_answer.hide()
        for i,b in enumerate(self.lesson_buttons):b.setStyleSheet(f"border-color:{GREEN if i==index else EDGE};")

    def build_workbench(self):
        lay=self.add_page();lay.addWidget(label("A radio link you can inspect.","title"))
        lay.addWidget(label("Run a known message through a sampled QPSK link. Change one condition and compare what the receiver actually observes.","muted"))
        w,pl=panel();row=QHBoxLayout();self.preset=QComboBox();self.preset.setObjectName("faultPreset");self.preset.addItems(["Healthy link","Frequency offset","Low SNR","Clipping / overload","Mixed impairments"]);self.preset.currentIndexChanged.connect(self.apply_preset)
        self.preset.addItem("Custom / saved settings")
        row.addWidget(label("Start from preset"));row.addWidget(self.preset,1);self.run_btn=QPushButton("Run experiment");self.run_btn.setObjectName("primary");self.run_btn.clicked.connect(self.run_current);row.addWidget(self.run_btn);pl.addLayout(row)
        controls=QHBoxLayout();self.snr=self.number(24,-8,45," dB");self.cfo=self.number(0,-5000,5000," Hz");self.gain=self.number(.65,.05,8," ×",2);self.rail=self.number(1,.1,8,"",2);self.seed=QSpinBox();self.seed.setRange(1,999999);self.seed.setValue(42)
        for text,widget in [("Noise reference SNR",self.snr),("Injected offset",self.cfo),("Signal gain",self.gain),("ADC rail",self.rail),("Random seed",self.seed)]:
            col=QVBoxLayout();col.addWidget(label(text,"muted"));col.addWidget(widget);controls.addLayout(col)
        self.correct=QCheckBox("Correct frequency offset");self.correct.setChecked(True);controls.addWidget(self.correct);pl.addLayout(controls);lay.addWidget(w)
        metrics=QHBoxLayout();self.metric_labels={}
        for key,title in [("packet_success_rate","CRC-verified packets"),("ber","Bit error rate"),("evm_pct","Reference EVM"),("cfo_est_hz","Estimated offset")]:
            w,ml=panel();ml.addWidget(label(title,"muted"));v=label("—","metric");ml.addWidget(v);self.metric_labels[key]=v;metrics.addWidget(w)
        lay.addLayout(metrics)
        plots=QHBoxLayout();self.constellation=Plot("Recovered payload symbols",True);self.spectrum=Plot("Received baseband spectrum");plots.addWidget(self.constellation,1);plots.addWidget(self.spectrum,1);lay.addLayout(plots,1)
        self.observations=label("Waiting for an experiment.","muted");lay.addWidget(self.observations)
        self.telemetry=QTextBrowser();self.telemetry.setMaximumHeight(140);lay.addWidget(self.telemetry)

    @staticmethod
    def number(value,low,high,suffix,decimals=1):
        w=QDoubleSpinBox();w.setRange(low,high);w.setDecimals(decimals);w.setValue(value);w.setSuffix(suffix);return w

    def apply_preset(self,index):
        if index>=5:return
        presets=[(26,0,.65,1),(24,1800,.65,1),(2,0,.65,4),(26,0,4.2,1),(3,2200,3.2,1)]
        a,b,c,d=presets[index];self.snr.setValue(a);self.cfo.setValue(b);self.gain.setValue(c);self.rail.setValue(d)

    def config(self):
        return SimulationConfig(seed=self.seed.value(),snr_db=self.snr.value(),cfo_hz=self.cfo.value(),gain=self.gain.value(),clip_level=self.rail.value(),correct_cfo=self.correct.isChecked())

    def run_current(self):
        try:
            self.result=run_simulation(self.config());self.status.setText("LOCAL WORKBENCH  /  SIMULATION ONLY");self.update_result();self.footer.setText("Experiment complete · synthetic sampled baseband · no hardware accessed")
        except Exception as exc:self.show_error(str(exc))

    def update_result(self,infer=True):
        r=self.result;m=r.metrics
        fmt=lambda value,pattern,suffix='': 'Unavailable' if value is None else format(value,pattern)+suffix
        self.metric_labels["packet_success_rate"].setText(f"{m['packets_ok']} / {m['packets_total']}" if m.get('packets_total') is not None else f"{m['packets_ok']} valid")
        self.metric_labels["ber"].setText(fmt(m.get('ber'),'.3g'))
        self.metric_labels["evm_pct"].setText(fmt(m.get('evm_pct'),'.1f','%'))
        self.metric_labels["cfo_est_hz"].setText(fmt(m.get('cfo_est_hz'),'+.1f',' Hz'))
        self.constellation.set_data(np.real(r.constellation),np.imag(r.constellation));self.spectrum.set_data(r.spectrum_frequency_hz/1000,r.spectrum_dbfs)
        rail=m.get('clipping_fraction');self.observations.setText(f"Receiver: {m.get('status','simulation')}    ·    Rail hits: {fmt(None if rail is None else rail*100,'.2f','%')}    ·    Pilot SNR: {fmt(m.get('snr_est_db'),'.1f',' dB')}    ·    Sync: {fmt(m.get('sync_score'),'.3f')}    ·    BER counts: {m.get('bit_errors')} / {m.get('bits_compared')} bits")
        rows=r.telemetry[:4];body="\n".join(f"sequence={v.get('sequence')}    uptime={v.get('uptime_ms')} ms    button={v.get('button')}    CRC OK" for v in rows)
        self.telemetry.setPlainText("RECOVERED TELEMETRY\n"+(body or "No CRC-valid telemetry recovered. Inspect synchronization and impairment evidence."))
        if infer:
            self.diagnosis=None
            self.diagnose_current()

    def build_ai(self):
        lay=self.add_page();lay.addWidget(label("Turn a diagnosis into an experiment.","title"));lay.addWidget(label("The model sees receiver features, not injected settings. Its vote support is not a calibrated probability or a component-level root cause.","muted"))
        row=QHBoxLayout();self.train_btn=QPushButton("Train & evaluate model");self.train_btn.setObjectName("primary");self.train_btn.clicked.connect(self.train_model);row.addWidget(self.train_btn)
        b=QPushButton("Load model JSON");b.clicked.connect(self.load_model_dialog);row.addWidget(b);b=QPushButton("Diagnose current experiment");b.clicked.connect(self.diagnose_current);row.addWidget(b);lay.addLayout(row)
        self.model_status=label("No trained model loaded. Train on session-separated synthetic experiments.","muted");lay.addWidget(self.model_status)
        w,pl=panel();pl.addWidget(label("CURRENT INFERENCE","eyebrow"));self.ai_label=label("Train the diagnostic model.","title");self.ai_evidence=label("");self.ai_next=label("");pl.addWidget(self.ai_label);pl.addWidget(self.ai_evidence);pl.addWidget(self.ai_next);lay.addWidget(w)
        self.follow_btn=QPushButton("Run the recommended simulated follow-up");self.follow_btn.clicked.connect(self.run_followup);self.follow_btn.setEnabled(False);lay.addWidget(self.follow_btn)
        self.follow_result=label("Follow-up results appear here after a controlled repeat.","muted");lay.addWidget(self.follow_result)
        self.eval_view=QTextBrowser();self.eval_view.setMinimumHeight(180);self.eval_view.setPlainText("Evaluation includes held-out sessions, a rules baseline, confusion matrix and per-class errors. All training and evaluation data in this release are synthetic.");lay.addWidget(self.eval_view,1)

    def load_default_model(self):
        for path in [ROOT/"artifacts/model/model.json",resource_root()/"artifacts/reference/model/model.json"]:
            if path.exists():
                try:self.set_model(path)
                except Exception as exc:self.model_status.setText(f"Model could not load: {exc}")
                break

    def set_model(self,path):
        from .diagnostics import load_model
        self.model=load_model(path);self.model_status.setText(f"Loaded {Path(path).name} · synthetic training only · session-held-out evaluation")
        ev=Path(path).with_name("evaluation.json")
        if ev.exists():self.evaluation=json.loads(ev.read_text());self.show_evaluation(self.evaluation)
        if self.result is not None:self.diagnose_current()

    def show_evaluation(self,evaluation):
        e=evaluation;esc=html.escape
        sessions=e.get('split_manifest',{}).get('split_sessions',{})
        coverage_note=f"Receiver measurement coverage across attempted inputs: {e.get('receiver_measurement_coverage',1):.1%}. Test acquisitions excluded from classification: {e.get('excluded_test_count',0)}. Their missing features are never invented."
        scores=f"<tr><td>Random forest</td><td>{e['model_macro_f1']:.3f}</td><td>{e['model_accuracy']:.1%}</td></tr><tr><td>Fixed diagnostic rules</td><td>{e['rules_macro_f1']:.3f}</td><td>{e['rules_accuracy']:.1%}</td></tr>"
        names=e['model']['class_order'];matrix=e['model']['confusion_matrix']
        header='<tr><th>Actual / predicted</th>'+''.join(f'<th>{esc(n.replace("_"," "))}</th>' for n in names)+'</tr>'
        cells=''.join('<tr><td>'+esc(name.replace('_',' '))+'</td>'+''.join(f'<td>{n}</td>' for n in row)+'</tr>' for name,row in zip(names,matrix))
        mixed=e.get('mixed_fault_probe',{});ab=e.get('abstention',{})
        self.eval_view.setHtml(f'''<style>body{{color:{TEXT};font-family:'Segoe UI';}}td,th{{padding:6px 14px;border-bottom:1px solid {EDGE};text-align:left;}}th{{color:{MUTED};}}h3{{color:{GREEN};}}</style><h3>Held-out synthetic evaluation</h3><p>{e['test_count']} acquired observations from {len(sessions.get('test',[]))} unseen virtual sessions. Training: {len(sessions.get('train',[]))} sessions. Validation: {len(sessions.get('validation',[]))} sessions.</p><p>{coverage_note}</p><table><tr><th>Method</th><th>Macro F1</th><th>Accuracy</th></tr>{scores}</table><p>Both methods are evaluated on the same acquired test inputs. These simple isolated faults do not demonstrate an AI advantage or real-hardware accuracy.</p><h3>Model confusion matrix · count of observations</h3><table>{header}{cells}</table><p>Accepted test coverage: {ab.get('test_coverage',0):.0%}. Mixed-fault probes rejected as unknown: {mixed.get('rejection_rate',0):.0%} of {mixed.get('count',0)} trials. This small probe is not a general unknown-fault benchmark.</p>''')

    def load_model_dialog(self):
        p,_=QFileDialog.getOpenFileName(self,"Load a diagnostic model",str(ROOT),"JSON model (*.json)")
        if p:
            try:self.set_model(Path(p))
            except Exception as exc:self.show_error(str(exc))

    def train_model(self):
        if self._worker is not None and self._worker.isRunning():return
        from .diagnostics import train_demo_model
        self.train_btn.setEnabled(False);self.model_status.setText("Training measured-feature model and evaluating held-out sessions…")
        self._worker=Worker(lambda:train_demo_model(ROOT/"artifacts/model",sessions=20,windows_per_class=3))
        self._worker.result.connect(self.training_done);self._worker.failed.connect(self.training_failed);self._worker.start()

    def training_done(self,result):
        self.train_btn.setEnabled(True)
        try:self.set_model(ROOT/"artifacts/model/model.json")
        except Exception as exc:self.show_error(str(exc))

    def training_failed(self,error):
        self.train_btn.setEnabled(True);self.model_status.setText("Training failed. Review the error and retry.");self.show_error(error)

    def diagnose_current(self):
        if self.result is None or self.model is None:
            return
        from .diagnostics import diagnose,rules_diagnose
        if not self.result.metrics.get('acquisition_valid',self.result.metrics.get('synchronization_valid',True)) or not self.result.metrics.get('reference_available',True) or any(value is None for value in self.result.features.values()):
            self.diagnosis=None;self.ai_label.setText('Receiver evidence only')
            self.ai_evidence.setText('This capture has no compatible payload reference or has not acquired a valid burst. The current model was trained on synthetic benchmark features, so it is not run on unavailable measurements.')
            self.ai_next.setText('NEXT MEASUREMENT\nConfirm the recording sample/symbol rates and presence of the RF Link Lab preamble. Inspect pilot quality and CRC outcomes before drawing a fault conclusion.');self.follow_btn.setEnabled(False);return
        meta=getattr(self.result,'metadata',{})
        if meta.get('known_payload_comparison') or meta.get('diagnosis_allowed') is False:
            self.diagnosis=None;self.ai_label.setText('Known-payload link measurements')
            self.ai_evidence.setText('This result compares a saved finite TX reference with captured samples. The current classifier has not been evaluated for conducted-link recordings, so no hardware fault diagnosis is inferred.');self.ai_next.clear();self.follow_btn.setEnabled(False);return
        if 'serial_waveform_software_loopback' in (meta.get('provenance'),meta.get('original_origin'),meta.get('source_provenance')):
            self.diagnosis=None;self.ai_label.setText('Serial-to-modem validation')
            self.ai_evidence.setText('CRC-valid serial packets were encoded and independently decoded in software. No RF transmission occurred. This input is not part of the classifier evaluation.');self.ai_next.clear();self.follow_btn.setEnabled(False);return
        try:
            self.diagnosis=diagnose(self.result.features,self.model)
            self.display_diagnosis(self.diagnosis,"CURRENT MODEL INFERENCE")
        except Exception as exc:self.show_error(str(exc))

    def display_diagnosis(self,d,scope):
        from .diagnostics import rules_diagnose
        if not d:
            self.ai_label.setText("No recorded inference.");self.ai_evidence.setText("Train or load a model, then explicitly diagnose this experiment.");self.ai_next.clear();self.follow_btn.setEnabled(False);return
        try:
            self.ai_label.setText(str(d['label']).replace('_',' ').title())
            evidence="\n".join(d.get('evidence',[]));probs=d.get('probabilities',{})
            votes=" · ".join(f"{k.replace('_',' ')} {v:.0%}" for k,v in probs.items())
            self.ai_evidence.setText(f"{scope}\nModel vote support: {d.get('support',0):.0%}\n{votes}\n\n{evidence}\nRules baseline: {rules_diagnose(self.result.features)}")
            self.ai_next.setText("NEXT MEASUREMENT\n"+d.get('next_measurement','')+("\nRejection: "+str(d['unknown_reason']) if d.get('unknown_reason') else ""));self.follow_btn.setEnabled(True)
        except Exception as exc:self.show_error(str(exc))

    def run_followup(self):
        if self.result is None or self.diagnosis is None:return
        if not isinstance(self.result.config,SimulationConfig):
            self.show_error('Controlled simulator interventions are available only for a current synthetic experiment.');return
        from .diagnostics import propose_followup
        try:
            before=self.result;before_d=self.diagnosis
            plan=propose_followup(before.config,before_d)
            cfg=plan['config'];allowed={f.name for f in dataclasses.fields(SimulationConfig)}
            self.result=run_simulation(SimulationConfig(**{k:v for k,v in cfg.items() if k in allowed}));self.status.setText("CONTROLLED FOLLOW-UP  /  SIMULATION ONLY");self.update_result()
            m,n=before.metrics,self.result.metrics
            self.follow_result.setText(f"SIMULATION-ONLY FOLLOW-UP\n{plan.get('rationale','')}\nChanged: {plan.get('changes',{})}\nCRC packets: {m['packets_ok']}/{m['packets_total']} → {n['packets_ok']}/{n['packets_total']}    EVM: {m['evm_pct']:.1f}% → {n['evm_pct']:.1f}%\nExpected: {plan.get('expected_observation','')}\nObserved signature: {before_d['label']} → {self.diagnosis['label']}")
            self.snr.setValue(cfg['snr_db']);self.cfo.setValue(cfg['cfo_hz']);self.gain.setValue(cfg['gain']);self.rail.setValue(cfg['clip_level']);self.seed.setValue(cfg['seed'])
        except Exception as exc:self.show_error(str(exc))

    def build_evidence(self):
        lay=self.add_page();lay.addWidget(label("Keep the evidence with the conclusion.","title"));lay.addWidget(label("Export the current waveform, settings, receiver metrics and AI inference. Reopen a run with a capture-integrity check.","muted"))
        row=QHBoxLayout();b=QPushButton("Save current experiment");b.setObjectName("primary");b.clicked.connect(self.export_dialog);row.addWidget(b);b=QPushButton("Open a saved run");b.clicked.connect(self.open_dialog);row.addWidget(b);lay.addLayout(row)
        self.export_status=label("A run bundle contains run.json, capture.npz and a standalone report.html.","muted");lay.addWidget(self.export_status)
        w,pl=panel();pl.addWidget(label("WHAT THIS RELEASE PROVES","eyebrow"));pl.addWidget(label("Reproducible software experiments, independent CRC decoding, controlled fault tests and synthetic model evaluation."));pl.addWidget(label("Saved-run replay displays recorded metrics; it does not recalculate the receiver. Hardware RF performance, independent-radio synchronization, calibrated sensitivity and real-hardware AI accuracy remain untested.","muted"));lay.addWidget(w);lay.addStretch()

    def export_dialog(self):
        if self.result is None:return
        p=QFileDialog.getExistingDirectory(self,"Save experiment bundle",str(ROOT/"artifacts"))
        if p:
            try:
                path=save_run(self.result,Path(p),self.diagnosis);self.export_status.setText(f"Saved {path}\nOpen report.html for the shareable experiment report.")
            except Exception as exc:self.show_error(str(exc))

    def open_dialog(self):
        p,_=QFileDialog.getOpenFileName(self,"Open a saved experiment",str(ROOT/"artifacts"),"Run manifest (run.json)")
        if p:
            try:
                self.result=load_run(Path(p));cfg=self.result.config
                self.preset.setCurrentIndex(5)
                if 'snr_db' in cfg:
                    self.snr.setValue(cfg['snr_db']);self.cfo.setValue(cfg['cfo_hz']);self.gain.setValue(cfg['gain']);self.rail.setValue(cfg['clip_level']);self.seed.setValue(cfg['seed']);self.correct.setChecked(cfg['correct_cfo'])
                self.update_result(infer=False);self.diagnosis=self.result.saved_diagnosis;self.display_diagnosis(self.diagnosis,"RECORDED INFERENCE FROM SAVED RUN");self.status.setText(f"SAVED {self.result.provenance.upper()}  /  HASH VERIFIED");self.export_status.setText(f"Loaded {p}. Showing recorded receiver metrics.");self.select_page(1)
            except Exception as exc:self.show_error(str(exc))

    def build_hardware(self):
        from .hardware_ui import HardwarePanel
        lay=self.add_page();self.hardware_panel=HardwarePanel(self.apply_external_result,self.apply_serial_result,self.show_error);lay.addWidget(self.hardware_panel)
        self.hardware_panel.conducted_panel.activity_changed.connect(self._link_activity)
        # Backwards-compatible identity for source tests and integrations.
        self.hardware_result=self.hardware_panel.setup_output

    def apply_external_result(self,result):
        self.result=result;self.diagnosis=None;self.preset.setCurrentIndex(5);self.update_result()
        if getattr(result,'metadata',{}).get('known_payload_comparison'):
            mode=result.metadata.get('experiment_mode','saved recording')
            self.status.setText('KNOWN-PAYLOAD I/Q DECODE  /  '+mode.upper())
        else:self.status.setText('INDEPENDENT I/Q DECODE  /  SOURCE RECORDED IN METADATA')
        self.footer.setText('File reprocessed through the receiver. Simulation controls configure the next synthetic run. Missing reference metrics remain unavailable.');self.select_page(1)

    def apply_serial_result(self,result):
        self.result=result;self.diagnosis=None;self.preset.setCurrentIndex(5);self.update_result()
        self.status.setText('SERIAL TELEMETRY → SOFTWARE MODEM  /  NO RF TRANSMISSION')
        self.footer.setText('Using CRC-verified serial payload bytes. The modem path was exercised in software only.');self.select_page(1)

    def probe(self):
        from .hardware import probe_bladerf
        self.hardware_result.setPlainText(json.dumps(probe_bladerf(),indent=2))

    def show_error(self,text):
        self.footer.setText("Error: "+text)
        QMessageBox.warning(self,"RF Link Lab",text)

    def closeEvent(self,event):
        if self._worker is not None and self._worker.isRunning():
            self.footer.setText("Training is still running. Close the window after it finishes.");event.ignore()
        elif not self.hardware_panel.shutdown():
            self.footer.setText('A measurement or saved-evidence job is still running. Close after it finishes.');event.ignore()
        else:super().closeEvent(event)


def launch():
    app=QApplication.instance() or QApplication([])
    app.setApplicationName("RF Link Lab")
    w=Window();w.show()
    return app.exec()
