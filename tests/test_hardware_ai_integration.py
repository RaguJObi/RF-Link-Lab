"""Evidence jobs and physical radio jobs must not overlap in the native panel."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from rf_link_lab.hardware_ui import HardwarePanel


@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    errors = []
    widget = HardwarePanel(lambda value: None, lambda value: None, errors.append)
    yield app, widget, errors
    assert widget.shutdown()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_new_evidence_tab_is_passive_and_defers_radio_work_during_a_file_job(panel, monkeypatch):
    app, widget, errors = panel
    assert widget.tabs.tabText(5) == "6 · Evidence & AI"
    assert widget.hardware_review_panel.review is None
    monkeypatch.setattr(widget.hardware_review_panel, "is_busy", lambda: True)
    widget.busy(lambda: pytest.fail("radio operation started"), lambda result: None)
    assert widget.task is None and "saved-evidence" in errors[-1]


def test_review_activity_disables_radio_controls_and_restores_them_after_completion(panel):
    app, widget, errors = panel
    widget.hardware_review_panel.activity_changed.emit(True)
    assert not widget.probe_btn.isEnabled() and not widget.capture_btn.isEnabled()
    assert not widget.conducted_panel.isEnabled()
    widget.hardware_review_panel.activity_changed.emit(False)
    assert widget.probe_btn.isEnabled() and widget.capture_btn.isEnabled()
    assert widget.conducted_panel.isEnabled()
    widget._conducted_activity(True)
    assert not widget.hardware_review_panel.isEnabled()
    widget._conducted_activity(False)
    assert widget.hardware_review_panel.isEnabled() and not errors


def test_parent_close_waits_for_review_without_stopping_the_serial_recorder(panel, monkeypatch):
    app, widget, errors = panel
    calls = []
    monkeypatch.setattr(widget.session, "stop", lambda: calls.append("serial stop"))
    original = widget.hardware_review_panel.shutdown
    monkeypatch.setattr(widget.hardware_review_panel, "shutdown", lambda: False)
    assert not widget.shutdown()
    assert calls == []
    monkeypatch.setattr(widget.hardware_review_panel, "shutdown", original)
