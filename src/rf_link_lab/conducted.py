"""Finite link experiments: prepared bytes, explicit transport, independent decode.

Offline rehearsal never discovers or opens a radio. Live execution requires a
selected device and a validated operator connection/power plan. A capture or
an exit status alone never establishes a decoded RF link.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path,PurePosixPath
import shutil
import threading

import numpy as np

from . import __version__
from .protocol import Telemetry

SCHEMA='rf-link-lab-conducted-experiment-v1'


def _utc():return datetime.now(timezone.utc).isoformat()


def _digest(path):
    digest=hashlib.sha256();size=0
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk);size+=len(chunk)
    return {'size_bytes':size,'sha256':digest.hexdigest()}


def _write_json(path,value):
    path=Path(path);temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8',newline='\n')
    temporary.replace(path)


def _asset(folder,name):
    if folder.is_symlink() or bool(getattr(folder,'is_junction',lambda:False)()):raise ValueError('Experiment folders cannot be links or junctions.')
    if (not isinstance(name,str) or not name or '\\' in name or ':' in name
            or PurePosixPath(name).is_absolute() or any(part in ('','.','..') for part in name.split('/'))):
        raise ValueError('Unsafe experiment evidence path.')
    path=folder
    for part in name.split('/'):
        path=path/part
        if path.is_symlink() or bool(getattr(path,'is_junction',lambda:False)()):
            raise ValueError('Experiment evidence cannot use links or junctions.')
    if not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError('Experiment evidence is missing or outside its folder.')
    return path


def example_payload():
    return [Telemetry(number,1000+number*100,bool(number%2)) for number in range(12)]


def load_uart_payload(path,max_frames=12):
    """Select historical CRC-verified physical serial rows; open no endpoint."""
    from .serial_logging import replay_uart_recording
    if type(max_frames) is not int or not 1<=max_frames<=64:raise ValueError('Select 1 to 64 payload frames.')
    path=Path(path);folder=path if path.is_dir() else path.parent
    replay=replay_uart_recording(folder)
    if not replay['matches']:raise ValueError('A complete, faithfully replayed UART recording is required.')
    rows=[json.loads(line) for line in (folder/'packets.jsonl').read_text(encoding='utf-8').splitlines()]
    rows=[row for row in rows if row['source']=='serial'][-max_frames:]
    if not rows:raise ValueError('This UART recording contains no physical-serial source packets.')
    return ([Telemetry(row['sequence'],row['uptime_ms'],row['button']) for row in rows],
            {'source':'recorded_serial','uart_manifest':str((folder/'uart.json').resolve()),
             'selected_rows':rows,'origin_note':'Recorded host serial receipt; board identity is not authenticated.'})


def _copy_tx(reference_path,destination):
    from .conducted_waveform import load_tx_reference
    source=load_tx_reference(reference_path);manifest=source['manifest']
    original=Path(reference_path);original=original if original.is_dir() else original.parent
    destination.mkdir()
    for name in ['tx.json']+[item['file'] for item in manifest['files'].values()]:
        source_path=_asset(original,name)
        with source_path.open('rb') as src,(destination/name).open('xb') as dst:shutil.copyfileobj(src,dst)
    copied=load_tx_reference(destination)
    if copied['manifest']!=manifest:raise ValueError('TX reference changed while copying.')
    return copied


def _rehearsal_capture(reference,folder,settings,cancel_event):
    """A labelled software channel applied to the exact serialized TX samples."""
    folder.mkdir();tx=reference['tx_iq'];fs=reference['manifest']['sample_rate']
    prefix=fs//100;count=prefix+len(tx)+fs//50
    # This software recording is intentionally shorter than the hardware budget.
    rng=np.random.default_rng(3102026)
    active=tx[np.abs(tx)>1e-4]
    power=float(np.mean(np.abs(active)**2)) if active.size else 1e-4
    noise=np.sqrt(power/(10**(30/10))/2)
    received=(rng.normal(0,noise,count)+1j*rng.normal(0,noise,count)).astype(np.complex64)
    phase=.2+2*np.pi*300*np.arange(len(tx))/fs
    received[prefix:prefix+len(tx)]+=.8*tx*np.exp(1j*phase)
    if cancel_event.is_set():return {'status':'cancelled','capture_completed':False,'cleanup_confirmed':True,'tx_start_requested':False,'paths':{},'error':'Offline rehearsal cancelled.'}
    components=np.rint(np.column_stack((received.real,received.imag))*2048)
    if np.any(components < -2048) or np.any(components > 2047):raise ValueError('Offline channel exceeded the SC16Q11 component range.')
    path=folder/'rx.sc16q11';path.write_bytes(components.astype('<i2').tobytes())
    sidecar={'schema':'rf-link-lab.duplex-rehearsal-rx.v1','source':'synthetic_duplex_rehearsal',
             'file':path.name,'format':'SC16Q11_LE','sample_rate':fs,'symbol_rate':reference['manifest']['symbol_rate'],
             'samples':count,'bytes':count*4,'sha256':_digest(path)['sha256'],
             'requested':{'sample_rate':fs},'rf_transmitted':False,'hardware_accessed':False,
             'channel':{'snr_db':30,'cfo_hz':300,'amplitude':.8,'phase_rad':.2,'delay_samples':prefix,'seed':3102026},
             'scope':'Software channel applied to saved quantized TX bytes; no radio or serial port opened.'}
    _write_json(path.with_suffix(path.suffix+'.json'),sidecar)
    return {'status':'complete','capture_completed':True,'cleanup_confirmed':True,'tx_start_requested':False,
            'hardware_accessed':False,'paths':{'rx':str(path),'rx_sidecar':str(path.with_suffix(path.suffix+'.json'))},'error':None}


def _summary(manifest):
    return {'declared_origin':manifest['origin'],'mode':manifest['mode'],'experiment_status':manifest['status'],
            'recorded_metrics':manifest.get('metrics',{}),'payload_source':manifest['payload_source'],
            'tx_start_requested':manifest.get('tx_start_requested',False),
            'cleanup_confirmed':manifest.get('cleanup_confirmed'),
            'transport':manifest.get('transport'), 'error':manifest.get('error'),
            'scope':'Known-payload measurements and recorded transport outcome; declarations are not authenticated.'}


def run_conducted_experiment(output_dir,*,tx_reference_path,mode='rehearsal',settings=None,
                             connection_plan=None,payload_source=None,cancel_event=None,duplex_runner=None):
    """Run one finite burst or software rehearsal, preserving failures and logs."""
    from .bench import BenchSession
    from .bladerf_duplex import DuplexSettings,run_finite_duplex
    from .conducted_plan import validate_connection_plan
    from .conducted_waveform import decode_known_capture,load_tx_reference
    from .preflight import doctor
    from .storage import save_run,json_value
    if mode not in ('rehearsal','live'):raise ValueError('Experiment mode must be rehearsal or live.')
    settings=settings if settings is not None else DuplexSettings(device_serial='')
    parameters=asdict(settings)
    prepared=load_tx_reference(tx_reference_path)
    if parameters['sample_rate']!=prepared['manifest']['sample_rate']:raise ValueError('Device and prepared TX sample rates must agree.')
    if prepared['manifest']['duration_s']>.5:raise ValueError('This experiment accepts at most a 0.5 second finite TX burst.')
    plan_validation=None
    if mode=='live':
        plan_validation=validate_connection_plan(connection_plan,frequency_hz=settings.frequency_hz,
                                                tx_gain_db=settings.tx_gain_db,device_serial=settings.device_serial)
    source=json.loads(json.dumps(payload_source or {'source':'explicit_prepared_payload'},allow_nan=False))
    cancel_event=cancel_event if cancel_event is not None else threading.Event()
    bench=BenchSession.create(Path(output_dir),title='Finite conducted link — '+mode,environment=doctor())
    record={'schema':SCHEMA,'application_version':__version__,'mode':mode,
            'origin':'synthetic_duplex_rehearsal' if mode=='rehearsal' else 'hardware_duplex_rx',
            'created_utc':_utc(),'finalized_utc':None,'finalized':False,'status':'running',
            'requested':parameters,'plan_validation':plan_validation,'payload_source':source,
            'tx_start_requested':False,'transport_invoked':False,'cleanup_confirmed':None,'transport':None,'metrics':{},'error':None,'assets':{}}
    manifest_path=bench.folder/'experiment.json';_write_json(manifest_path,record)
    receiver_manifest=None
    try:
        reference=_copy_tx(tx_reference_path,bench.folder/'tx')
        if cancel_event.is_set():
            record.update(status='cancelled',error='Cancelled before transport start.',cleanup_confirmed=True)
        else:
            if mode=='rehearsal':transport=_rehearsal_capture(reference,bench.folder/'rehearsal',settings,cancel_event)
            else:
                record['transport_invoked']=True
                transport=(duplex_runner or run_finite_duplex)(bench.folder/'tx/tx.sc16q11',bench.folder/'hardware',settings,connection_plan=connection_plan,cancel_event=cancel_event)
            record.update(transport=json_value(transport),tx_start_requested=bool(transport.get('tx_start_requested')),
                          cleanup_confirmed=transport.get('cleanup_confirmed'),error=transport.get('error'))
            if transport['status']!='complete' or not transport.get('capture_completed'):
                record['status']='cancelled' if transport['status']=='cancelled' else 'failed'
            elif cancel_event.is_set():record.update(status='cancelled',error='Cancelled before receiver processing.')
            else:
                if mode=='live':
                    if not record['tx_start_requested'] or record['cleanup_confirmed'] is not True:
                        raise ValueError('Completed live transport requires a TX request and confirmed cleanup.')
                    offered=Path(transport['paths']['tx'])
                    expected=reference['manifest']['files']['waveform']['sha256']
                    if transport.get('tx_sha256')!=expected or _digest(offered)['sha256']!=expected:
                        raise ValueError('Actual offered TX file differs from the saved known-payload reference.')
                capture=Path(transport['paths']['rx'])
                bench.add_iq_recording(capture)
                result=decode_known_capture(capture,bench.folder/'tx',sample_rate=settings.sample_rate)
                result.metadata.update(experiment_mode=mode,payload_source=source,diagnosis_allowed=False,
                                       experiment_manifest='experiment.json',rf_transmitted=None if mode=='live' else False,
                                       rf_transmission_requested=mode=='live' and record['tx_start_requested'],
                                       rf_success='unassessed' if mode=='live' else 'not_applicable')
                result.config=dict(result.metadata)
                receiver_manifest=save_run(result,bench.folder/'receiver')
                bench.add_iq_recording(receiver_manifest)
                record['metrics']=json_value(result.metrics)
                record['status']='decoded' if result.metrics.get('known_payload_complete') else 'capture_without_complete_payload'
        if source.get('uart_manifest'):
            try:bench.add_uart_recording(Path(source['uart_manifest']))
            except (ValueError,OSError) as exc:bench.add_note('Payload UART recording not attached: '+str(exc))
    except KeyboardInterrupt:
        cancel_event.set();record.update(status='cancelled',error='Interrupted by operator.')
        if mode=='live' and record['transport_invoked'] and record['transport'] is None:
            record.update(tx_start_requested=None,cleanup_confirmed=False)
        bench.add_note('Experiment interrupted; inspect transport cleanup status before another physical run.')
    except Exception as exc:
        record.update(status='failed',error=f'{type(exc).__name__}: {exc}')
        if mode=='live' and record['transport_invoked'] and record['transport'] is None:
            record.update(tx_start_requested=None,cleanup_confirmed=False)
        bench.add_note('Experiment failed: '+record['error'])
    for directory in ('tx','hardware','rehearsal','receiver'):
        for path in sorted((bench.folder/directory).rglob('*')):
            if path.is_file():
                relative=path.relative_to(bench.folder).as_posix();record['assets'][relative]=_digest(_asset(bench.folder,relative))
    record.update(finalized=True,finalized_utc=_utc())
    _write_json(manifest_path,record)
    bench.add_experiment_recording(manifest_path)
    bench_manifest=bench.finalize()
    return {'status':record['status'],'mode':mode,'experiment_manifest':str(manifest_path),
            'bench_manifest':str(bench_manifest),'receiver_manifest':str(receiver_manifest) if receiver_manifest else None,
            'metrics':record['metrics'],'error':record['error'],'cleanup_confirmed':record['cleanup_confirmed']}


def verify_conducted_experiment(path):
    """Check stored assets and declared status; do not transmit or rerun decode."""
    path=Path(path);path=path/'experiment.json' if path.is_dir() else path
    folder=path.parent
    if path.name!='experiment.json':raise ValueError('Choose experiment.json or its folder.')
    manifest=json.loads(_asset(folder,'experiment.json').read_text(encoding='utf-8'))
    if manifest.get('schema')!=SCHEMA or manifest.get('finalized') is not True:raise ValueError('Unsupported or unfinished conducted experiment.')
    if manifest.get('mode') not in ('rehearsal','live') or manifest.get('status') not in ('decoded','capture_without_complete_payload','failed','cancelled'):
        raise ValueError('Invalid conducted experiment mode or outcome.')
    if manifest['mode']=='rehearsal' and manifest.get('tx_start_requested'):raise ValueError('An offline rehearsal cannot declare physical TX.')
    expected_origin='synthetic_duplex_rehearsal' if manifest['mode']=='rehearsal' else 'hardware_duplex_rx'
    if manifest.get('origin')!=expected_origin:raise ValueError('Experiment origin and mode disagree.')
    assets=manifest.get('assets')
    if not isinstance(assets,dict):raise ValueError('Experiment asset inventory is missing.')
    for name,expected in assets.items():
        if _digest(_asset(folder,name))!=expected:raise ValueError('Experiment asset hash or size mismatch: '+name)
    if manifest['status'] in ('decoded','capture_without_complete_payload'):
        required={'tx/tx.json','tx/tx.sc16q11','tx/tx-reference.npz','receiver/run.json','receiver/capture.npz'}
        if not required<=assets.keys():raise ValueError('A decoded experiment requires preserved TX and receiver evidence.')
        if manifest['mode']=='live' and (not manifest.get('tx_start_requested') or not manifest.get('cleanup_confirmed')):
            raise ValueError('A completed live experiment requires confirmed finite transport cleanup.')
        receiver=json.loads(_asset(folder,'receiver/run.json').read_text(encoding='utf-8'))
        if receiver.get('metrics')!=manifest.get('metrics'):
            raise ValueError('Experiment metrics disagree with preserved receiver measurements.')
        if (manifest['status']=='decoded')!=(receiver['metrics'].get('known_payload_complete') is True):
            raise ValueError('Experiment outcome disagrees with known-payload coverage.')
        if receiver.get('metadata',{}).get('experiment_mode')!=manifest['mode']:
            raise ValueError('Receiver experiment mode disagrees with experiment record.')
        transport=manifest.get('transport') or {}
        if transport.get('status')!='complete' or transport.get('capture_completed') is not True:
            raise ValueError('A decoded experiment requires a completed finite recording.')
        raw='hardware/rx.sc16q11' if manifest['mode']=='live' else 'rehearsal/rx.sc16q11'
        if raw not in assets:raise ValueError('A decoded experiment requires its raw recording.')
        if manifest['mode']=='live':
            tx=json.loads(_asset(folder,'tx/tx.json').read_text(encoding='utf-8'))
            if transport.get('tx_sha256')!=tx['files']['waveform']['sha256'] or 'hardware/tx.sc16q11' not in assets:
                raise ValueError('Live transport must preserve the offered known TX bytes.')
            if assets['hardware/tx.sc16q11']['sha256']!=transport['tx_sha256']:
                raise ValueError('Offered TX file and transport hash disagree.')
    return {'status':'verified','manifest':manifest,'summary':_summary(manifest),'assets_verified':len(assets),
            'experiment_status':manifest['status'],'source_authenticated':False}
