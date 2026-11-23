"""Import external samples and perform genuine offline receiver processing."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

import numpy as np

from .hardware import load_sc16q11
from .receiver import decode_iq,resample_iq
from .modem import qpsk_demodulate


def decode_recording(path: Path, *, sample_rate: int | None = None,
                     symbol_rate: int=50000, correct_cfo: bool=True,
                     use_saved_reference: bool=False, resample_to: int | None=200000):
    """Decode saved I/Q without calling the transmitter or simulator.

    Origin labels are sidecar declarations, not independent evidence of RF.
    Missing payload references produce unavailable benchmark BER/reference EVM.
    """
    path=Path(path)
    if path.is_dir():path=path/'run.json'
    reference=None
    if path.suffix=='.json' and path.name=='run.json':
        manifest=json.loads(path.read_text(encoding='utf-8'))
        if manifest.get('schema') not in ('rf-link-lab-run-v1','rf-link-lab-run-v2'):
            raise ValueError('Unsupported run manifest.')
        archive=path.with_name('capture.npz')
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=manifest.get('capture_sha256'):
            raise ValueError('Capture hash mismatch; the saved waveform has changed.')
        config=manifest['config']
        with np.load(archive,allow_pickle=False) as data:
            samples=data['rx_iq'].copy()
            if use_saved_reference and 'reference_symbols' in data and data['reference_symbols'].size:
                symbols=data['reference_symbols'].copy()
                bits=data['reference_bits'].copy() if 'reference_bits' in data else qpsk_demodulate(symbols)
                reference={'reference_bits':bits,'reference_symbols':symbols}
        declared=manifest.get('metadata',{})
        metadata={**declared,'sample_rate':sample_rate or config.get('sample_rate'),
                  'samples_per_symbol':config.get('samples_per_symbol',4),
                  'correct_cfo':correct_cfo,'source_path':str(path.resolve()),
                  'source_provenance':manifest.get('provenance','unknown'),'sha256_verified':True}
        metadata['original_origin']=declared.get('original_origin',declared.get('source_provenance',manifest.get('provenance','unknown')))
        if use_saved_reference:
            metadata['expected_frames']=config.get('frames',declared.get('expected_frames'))
    elif path.suffix.lower() in ('.sc16q11','.bin','.iq'):
        samples=load_sc16q11(path)
        sidecar=path.with_suffix(path.suffix+'.json')
        saved=json.loads(sidecar.read_text(encoding='utf-8')) if sidecar.exists() else {}
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if saved.get('sha256') and saved['sha256']!=digest:
            raise ValueError('Capture hash mismatch; raw recording differs from its sidecar.')
        rate=sample_rate or saved.get('sample_rate') or saved.get('requested',{}).get('sample_rate')
        if not rate:raise ValueError('Enter the recording sample rate in complex samples/s.')
        metadata={'sample_rate':int(rate),'symbol_rate':symbol_rate,'correct_cfo':correct_cfo,
                  'source_path':str(path.resolve()),'source_provenance':saved.get('provenance',saved.get('source','external_file_origin_unverified')),
                  'original_origin':saved.get('provenance',saved.get('source','external_file_origin_unverified')),
                  'capture_sidecar':saved,'sha256_verified':bool(saved.get('sha256')),
                  'sampling_rate_source':'user_or_requested_settings; inspect actual hardware readback',
                  'capture_sha256':digest}
    else:raise ValueError('Choose run.json or a raw SC16 Q11 recording.')
    if resample_to and metadata['sample_rate']!=resample_to:
        samples,metadata=resample_iq(samples,metadata,resample_to)
    metadata['provenance']='independent_offline_decode'
    metadata['reference_requested']=bool(use_saved_reference)
    return decode_iq(samples,metadata,reference=reference)
