import json
import numpy as np
import pytest

from rf_link_lab.simulation import SimulationConfig,run_simulation
from rf_link_lab.storage import save_run,load_run


def test_bundle_roundtrip_and_report(tmp_path):
    original=run_simulation(SimulationConfig(seed=17,frames=3))
    path=save_run(original,tmp_path)
    replay=load_run(path)
    np.testing.assert_array_equal(original.rx_iq,replay.rx_iq)
    assert replay.metrics==original.metrics
    assert 'synthetic' in (tmp_path/'report.html').read_text().lower()
    assert json.loads(path.read_text())['capture_sha256']


def test_tampered_capture_rejected(tmp_path):
    save_run(run_simulation(SimulationConfig(frames=2)),tmp_path)
    with (tmp_path/'capture.npz').open('ab') as stream:stream.write(b'changed')
    with pytest.raises(ValueError,match='hash mismatch'):load_run(tmp_path)


def test_foreign_provenance_rejected(tmp_path):
    path=save_run(run_simulation(SimulationConfig(frames=2)),tmp_path)
    manifest=json.loads(path.read_text());manifest['provenance']='hardware'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='Unsupported'):load_run(path)
