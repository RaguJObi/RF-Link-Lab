"""Render the native hardware-evidence panel using retained physical files only."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from rf_link_lab.hardware_review_ui import HardwareReviewPanel
from rf_link_lab.ui import STYLE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casebook", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    # Match the main window's font registration on Qt's offscreen platform.
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"):
        if (fonts / name).is_file():
            QFontDatabase.addApplicationFont(str(fonts / name))
    errors = []
    widget = HardwareReviewPanel(errors.append)
    widget.setStyleSheet(STYLE)
    widget.setWindowTitle("RF Link Lab — saved hardware evidence")
    widget.resize(1280, 960)
    widget.show()

    def wait():
        deadline = time.monotonic() + 60
        while widget.is_busy() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        app.processEvents()
        if widget.is_busy() or errors:
            raise RuntimeError(str(errors) or "Saved-file job did not finish.")

    bench = ROOT / "artifacts/bench/physical-rf-20261020-buffer32768-imported"
    widget.experiment_path.setText(str(bench / "rf-run-4"))
    widget.receiver_path.setText(str(bench / "rf-run-4-carrier-tracking-reprocess/receiver"))
    widget.review_btn.click()
    wait()
    assert widget.review["outcome"]["selected_known_payload_complete"] is True
    assert widget.review["raw_adc_rail_measurement"]["sample_count"] == 3_000_000
    assert widget.grab().save(str(args.output / "10-hardware-evidence.png"))
    widget.casebook_path.setText(str(args.casebook.absolute()))
    widget.tabs.setCurrentIndex(1)
    widget.load_btn.click()
    wait()
    assert len(widget.case_records) == 5
    assert widget.operator_label.currentText() == "unknown"
    assert widget.grab().save(str(args.output / "11-hardware-cases.png"))
    widget.tabs.setCurrentIndex(2)
    widget.readiness_btn.click()
    wait()
    assert widget.last_readiness["status"] == "blocked"
    widget.diagnose_btn.click()
    wait()
    assert widget.diagnosis["label"] == "unknown"
    assert widget.grab().save(str(args.output / "12-hardware-readiness.png"))
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    print("Rendered three native panels from saved real evidence; training blocked and prediction unknown. No device or simulator action.")


if __name__ == "__main__":
    main()
