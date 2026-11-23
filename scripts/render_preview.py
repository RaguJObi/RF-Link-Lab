"""Render and exercise the actual desktop application for release QA."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from pathlib import Path
from PySide6.QtWidgets import QApplication
from rf_link_lab.ui import Window,ROOT

app=QApplication([])
w=Window();w.show();app.processEvents();w.run_current()
out=ROOT/'artifacts/preview';out.mkdir(parents=True,exist_ok=True)
for index,name in [(0,'01-learn'),(1,'02-workbench'),(2,'03-ai-diagnosis')]:
    w.select_page(index);app.processEvents();w.grab().save(str(out/f'{name}.png'))
w.preset.setCurrentIndex(3);w.run_current();w.select_page(2);app.processEvents()
w.grab().save(str(out/'04-fault.png'))
if w.model is not None:
    w.run_followup();app.processEvents();w.grab().save(str(out/'05-follow-up.png'))
w.select_page(4);app.processEvents();w.grab().save(str(out/'06-connect-boards.png'))
w.hardware_panel.serial_demo()
from PySide6.QtWidgets import QTabWidget
w.hardware_panel.findChild(QTabWidget).setCurrentIndex(1);app.processEvents()
w.grab().save(str(out/'07-serial.png'))
from uuid import uuid4
w.hardware_panel.bench_title.setText('Offline logging rehearsal')
w.hardware_panel.start_bench(ROOT/'artifacts/bench'/f'preview-{uuid4().hex[:12]}')
w.hardware_panel.serial_demo()
w.hardware_panel.bench_note.setText('Offline parser rehearsal. No physical board was connected.')
w.hardware_panel.add_bench_note();w.hardware_panel.finish_bench()
w.hardware_panel.tabs.setCurrentIndex(3);app.processEvents()
w.grab().save(str(out/'08-bench-session.png'))
import time
link=w.hardware_panel.conducted_panel
link.prepare_waveform(ROOT/'artifacts/conducted'/f'preview-tx-{uuid4().hex[:12]}')
assert link.start_rehearsal(ROOT/'artifacts/conducted'/f'preview-link-{uuid4().hex[:12]}')
deadline=time.monotonic()+30
while link.is_busy() and time.monotonic()<deadline:
    app.processEvents();time.sleep(.01)
assert not link.is_busy() and link.last_result['status']=='decoded'
w.hardware_panel.tabs.setCurrentIndex(4);app.processEvents()
w.grab().save(str(out/'09-conducted-rehearsal.png'))
w.close()
print(out)
