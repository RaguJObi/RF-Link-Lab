"""Command line interface for reproducible experiments and explicit RX bring-up."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def record_uart(output,port,*,duration=30.0,baudrate=115200):
    """Bounded explicit hardware recording; finalize evidence even on failure."""
    import math
    import time
    from .serial_link import SerialTelemetrySession
    if not math.isfinite(duration) or not 0.1<=duration<=86400:
        raise ValueError('Recording duration must be from 0.1 to 86400 seconds.')
    if not isinstance(port,str) or not port.strip():raise ValueError('Choose an explicit serial port.')
    if isinstance(baudrate,bool) or not isinstance(baudrate,int) or not 1200<=baudrate<=3000000:
        raise ValueError('Baud rate must be from 1200 to 3000000.')
    session=SerialTelemetrySession();session.start_recording(Path(output));error=None;interrupted=False
    try:
        session.start(port,baudrate)
        deadline=time.monotonic()+duration
        while time.monotonic()<deadline:
            snapshot=session.snapshot()
            if not snapshot['connected']:error=snapshot.get('last_error') or 'Serial reader stopped.';break
            recording=snapshot.get('recording') or {}
            if recording.get('status')!='active':error=recording.get('last_error') or 'UART recording stopped.';break
            time.sleep(min(.1,max(0,deadline-time.monotonic())))
    except KeyboardInterrupt:interrupted=True
    except (RuntimeError,OSError,ValueError) as exc:error=str(exc)
    finally:
        try:session.stop()
        except Exception as exc:error=error or str(exc)
        manifest=session.stop_recording()
    return {'succeeded':not error and not interrupted,'interrupted':interrupted,'error':error,
            'recording':manifest,'scope':'Explicit receive-only serial recording. No RF transmission or flashing.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description="RF Link Lab: telemetry measurements, saved evidence and RF diagnosis")
    sub=parser.add_subparsers(dest="command")
    sub.add_parser("gui",help="Open the native learning workbench; no hardware accessed")
    demo=sub.add_parser("demo",help="Run a synthetic framed QPSK experiment")
    demo.add_argument("--output",type=Path,default=Path("artifacts/demo"))
    demo.add_argument("--fault",choices=["healthy","frequency_offset","low_snr","clipping","mixed"],default="healthy")
    demo.add_argument("--seed",type=int,default=42)
    demo.add_argument("--model",type=Path)
    train=sub.add_parser("train",help="Train a measured-feature model with session-held-out evaluation")
    train.add_argument("--output",type=Path,default=Path("artifacts/model"))
    train.add_argument("--sessions",type=int,default=20)
    train.add_argument("--windows-per-class",type=int,default=3)
    train.add_argument("--seed",type=int,default=2026)
    replay=sub.add_parser("replay",help="Verify a stored synthetic run and display recorded metrics")
    replay.add_argument("path",type=Path)
    sub.add_parser("probe",help="Explicit read-only bladeRF identification; no TX")
    sub.add_parser('serial-ports',help='List serial endpoints without opening them')
    sub.add_parser('serial-demo',help='Exercise serial CRC/recovery without hardware')
    uart=sub.add_parser('serial-record',help='Record a selected serial port for a bounded duration, retaining raw bytes and events')
    uart.add_argument('--port',required=True);uart.add_argument('--output',type=Path,required=True)
    uart.add_argument('--duration',type=float,default=30.0);uart.add_argument('--baudrate',type=int,default=115200)
    uart_replay=sub.add_parser('uart-replay',help='Verify and replay a UART recording without opening a port')
    uart_replay.add_argument('path',type=Path)
    bench_demo=sub.add_parser('bench-demo',help='Create a complete offline bench report without hardware')
    bench_demo.add_argument('--output',type=Path,required=True)
    bench_verify=sub.add_parser('bench-verify',help='Verify a saved bench report and every linked evidence hash')
    bench_verify.add_argument('path',type=Path)
    review=sub.add_parser('hardware-review',help='Review a finalized saved physical RF experiment; no device access')
    review.add_argument('path',type=Path)
    review.add_argument('--receiver-run',type=Path,help='Optional separately saved decode of the same physical capture')
    review.add_argument('--output',type=Path,help='New folder for the review and HTML report')
    review_verify=sub.add_parser('hardware-review-verify',help='Verify a saved hardware-review export')
    review_verify.add_argument('path',type=Path)
    case_new=sub.add_parser('casebook-new',help='Create an empty operator-labelled hardware casebook')
    case_new.add_argument('--output',type=Path,required=True)
    case_add=sub.add_parser('casebook-add',help='Preserve a verified hardware review and an explicit operator annotation')
    case_add.add_argument('casebook',type=Path);case_add.add_argument('experiment',type=Path)
    case_add.add_argument('--receiver-run',type=Path)
    case_add.add_argument('--session-id',required=True,help='Physical bench session shared by related captures and reprocessings')
    labels=['unknown','healthy','frequency_offset','low_snr','clipping','transport_fault','receiver_processing','other']
    case_add.add_argument('--label',choices=labels,default='unknown')
    case_add.add_argument('--notes',default='')
    case_add.add_argument('--for-training',action='store_true',help='Explicitly select this processing variant for training')
    case_label=sub.add_parser('casebook-label',help='Append an operator annotation revision to a saved case')
    case_label.add_argument('casebook',type=Path);case_label.add_argument('case_id')
    case_label.add_argument('--label',choices=labels,required=True);case_label.add_argument('--notes',required=True)
    case_label.add_argument('--for-training',action='store_true')
    for name,description in [('casebook-audit','Verify casebook evidence, annotations and capture/session grouping'),
                             ('hardware-readiness','Check whether independently grouped hardware data can train a model')]:
        command=sub.add_parser(name,help=description);command.add_argument('casebook',type=Path)
    case_export=sub.add_parser('casebook-export',help='Export verified hardware cases and a separate blind feature vector')
    case_export.add_argument('casebook',type=Path);case_export.add_argument('--output',type=Path,required=True)
    hw_train=sub.add_parser('hardware-train',help='Train and evaluate a hardware-only model when independent-session gates pass')
    hw_train.add_argument('casebook',type=Path);hw_train.add_argument('--output',type=Path,required=True)
    hw_train.add_argument('--seed',type=int,default=2026)
    hw_diagnose=sub.add_parser('hardware-diagnose',help='Review saved physical evidence and request a qualified hardware-model prediction')
    hw_diagnose.add_argument('path',type=Path);hw_diagnose.add_argument('--receiver-run',type=Path)
    hw_diagnose.add_argument('--model',type=Path,required=True);hw_diagnose.add_argument('--output',type=Path)
    link_prepare=sub.add_parser('link-prepare',help='Prepare one finite SC16Q11 TX burst without opening hardware')
    link_prepare.add_argument('--output',type=Path,required=True)
    link_prepare.add_argument('--uart-recording',type=Path,help='Use CRC-verified packets from a finalized physical-serial recording')
    link_prepare.add_argument('--sample-rate',type=int,default=1000000)
    for command,help_text in [('link-rehearse','Apply a labelled software channel to a saved TX burst; no RF'),('link-live','Explicit finite conducted TX1/RX1 using a complete connection/power plan')]:
        link=sub.add_parser(command,help=help_text);link.add_argument('tx_reference',type=Path)
        link.add_argument('--output',type=Path,required=True)
        if command=='link-live':
            link.add_argument('--plan',type=Path,required=True)
            link.add_argument('--rx-gain',type=int,default=0,help='Requested overall RX gain in dB')
            link.add_argument('--rx-samples',type=int,help='Finite RX sample count; default up to three seconds within the project limit')
            link.add_argument('--bandwidth',type=int,default=200000,help='Requested analog bandwidth in Hz')
    link_verify=sub.add_parser('link-verify',help='Verify saved finite-link experiment files without hardware')
    link_verify.add_argument('path',type=Path)
    link_decode=sub.add_parser('link-decode',help='Compare an existing RX capture with its saved known TX reference')
    link_decode.add_argument('capture',type=Path);link_decode.add_argument('tx_reference',type=Path)
    link_decode.add_argument('--output',type=Path,required=True)
    sub.add_parser('doctor',help='Check local setup without opening devices')
    smoke=sub.add_parser('self-test',help='Run deterministic software checks in source or a portable bundle')
    smoke.add_argument('--output',type=Path)
    smoke.add_argument('--with-ui',action='store_true',help='Render and exercise actual native widgets without opening devices')
    decode=sub.add_parser('decode',help='Reprocess supplied I/Q through the independent receiver')
    decode.add_argument('path',type=Path);decode.add_argument('--sample-rate',type=int)
    decode.add_argument('--symbol-rate',type=int,default=50000);decode.add_argument('--with-reference',action='store_true')
    decode.add_argument('--output',type=Path)
    capture=sub.add_parser("capture-rx",help="Explicit finite RX-only bladeRF capture; no TX")
    capture.add_argument("output",type=Path)
    capture.add_argument("--frequency",type=int,required=True,help="Center frequency in Hz")
    capture.add_argument("--sample-rate",type=int,default=1000000,help="Complex samples/s")
    capture.add_argument("--samples",type=int,default=1000000)
    capture.add_argument("--gain",type=int,default=0,help="Requested receiver gain in dB")
    args=parser.parse_args(argv)
    exit_code=0
    try:
        if args.command in (None,"gui"):
            from .ui import launch
            return launch()
        if args.command=='doctor':
            from .preflight import doctor
            result=doctor()
        elif args.command=='self-test':
            from .preflight import self_test
            result=self_test(args.output,with_ui=args.with_ui)
            print(json.dumps(result,indent=2))
            return 0 if result['passed'] else 1
        elif args.command=='serial-ports':
            from .serial_link import list_ports
            result=list_ports()
        elif args.command=='serial-demo':
            from .serial_link import demo_serial_session
            result=demo_serial_session()
        elif args.command=='serial-record':
            result=record_uart(args.output,args.port,duration=args.duration,baudrate=args.baudrate)
            exit_code=130 if result['interrupted'] else (0 if result['succeeded'] else 1)
        elif args.command=='uart-replay':
            from .serial_logging import replay_uart_recording
            result=replay_uart_recording(args.path)
            exit_code=0 if result['matches'] else 1
        elif args.command=='bench-demo':
            from .bench import demo_bench_session
            manifest=Path(demo_bench_session(args.output))
            result={'manifest':str(manifest.resolve()),'report':str(manifest.parent/'report.html'),
                    'scope':'Offline UART and synthetic I/Q example. No device opened or RF transmitted.'}
        elif args.command=='bench-verify':
            from .bench import verify_bench_session
            result=verify_bench_session(args.path)
        elif args.command in ('hardware-review','hardware-diagnose'):
            from .hardware_review import review_experiment
            from .hardware_reports import save_hardware_review
            review=review_experiment(args.path,receiver_run=args.receiver_run)
            diagnosis=None
            if args.command=='hardware-diagnose':
                from .hardware_learning import diagnose_hardware
                diagnosis=diagnose_hardware(review,args.model)
            result={'review':review}
            if diagnosis is not None:result['diagnosis']=diagnosis
            if args.output:result['export']=save_hardware_review(review,args.output,diagnosis=diagnosis)
        elif args.command=='hardware-review-verify':
            from .hardware_reports import verify_hardware_review_report
            result=verify_hardware_review_report(args.path)
        elif args.command=='casebook-new':
            from .hardware_cases import create_casebook
            result=create_casebook(args.output)
        elif args.command=='casebook-add':
            from .hardware_cases import add_case
            result=add_case(args.casebook,args.experiment,session_id=args.session_id,receiver_run=args.receiver_run,
                            label=args.label,notes=args.notes,include_for_training=args.for_training)
        elif args.command=='casebook-label':
            from .hardware_cases import annotate_case
            result=annotate_case(args.casebook,args.case_id,label=args.label,notes=args.notes,
                                 include_for_training=args.for_training)
        elif args.command=='casebook-audit':
            from .hardware_cases import audit_casebook
            result=audit_casebook(args.casebook)
        elif args.command=='casebook-export':
            from .hardware_cases import export_dataset
            result=export_dataset(args.casebook,args.output)
        elif args.command=='hardware-readiness':
            from .hardware_learning import training_readiness
            result=training_readiness(args.casebook)
            exit_code=3 if result.get('status')=='blocked' else 0
        elif args.command=='hardware-train':
            from .hardware_learning import train_hardware_model
            result=train_hardware_model(args.casebook,args.output,seed=args.seed)
            exit_code=3 if result.get('status')=='blocked' else 0
        elif args.command=='link-prepare':
            from .conducted import example_payload,load_uart_payload
            from .conducted_waveform import prepare_tx_waveform
            messages,source=load_uart_payload(args.uart_recording) if args.uart_recording else (example_payload(),{'source':'offline_example_payload'})
            path=prepare_tx_waveform(messages,args.output,sample_rate=args.sample_rate)
            (Path(path).parent/'payload-source.json').write_text(json.dumps(source,indent=2),encoding='utf-8')
            result={'tx_manifest':str(path),'scope':'Prepared finite bytes only; no hardware opened or RF transmitted.','payload_source':source}
        elif args.command in ('link-rehearse','link-live'):
            from .conducted import run_conducted_experiment
            from .conducted_waveform import load_tx_reference
            from .bladerf_duplex import DuplexSettings
            reference=load_tx_reference(args.tx_reference)
            folder=args.tx_reference if args.tx_reference.is_dir() else args.tx_reference.parent
            source_path=folder/'payload-source.json'
            source=json.loads(source_path.read_text(encoding='utf-8')) if source_path.exists() else {'source':'explicit_prepared_payload'}
            plan=json.loads(args.plan.read_text(encoding='utf-8')) if args.command=='link-live' else None
            if args.command=='link-live' and (not isinstance(plan,dict) or not isinstance(plan.get('tx_power_bound'),dict)):
                raise ValueError('Live TX requires a complete connection/power plan; see CONDUCTED-PLAN.md.')
            rx_count=getattr(args,'rx_samples',None)
            settings=DuplexSettings(device_serial=plan.get('device_serial','') if plan else '',frequency_hz=plan['tx_power_bound'].get('frequency_hz',0) if plan else 915000000,
                                    tx_gain_db=plan['tx_power_bound'].get('tx_gain_db',0) if plan else -20,
                                    sample_rate=reference['manifest']['sample_rate'],
                                    rx_samples=min(reference['manifest']['sample_rate']*3,5000000) if rx_count is None else rx_count,
                                    rx_gain_db=getattr(args,'rx_gain',0),bandwidth_hz=getattr(args,'bandwidth',200000))
            result=run_conducted_experiment(args.output,tx_reference_path=args.tx_reference,mode='live' if plan else 'rehearsal',settings=settings,connection_plan=plan,payload_source=source)
            exit_code=0 if result['status']=='decoded' else 1
        elif args.command=='link-verify':
            from .conducted import verify_conducted_experiment
            result=verify_conducted_experiment(args.path)
        elif args.command=='link-decode':
            from .conducted_waveform import decode_known_capture
            from .storage import save_run
            if args.output.exists():raise FileExistsError('Choose a new decoded-run folder.')
            run=decode_known_capture(args.capture,args.tx_reference)
            result={'receiver_manifest':str(save_run(run,args.output)),'metrics':run.metrics}
        elif args.command=='decode':
            from .recordings import decode_recording
            run=decode_recording(args.path,sample_rate=args.sample_rate,symbol_rate=args.symbol_rate,use_saved_reference=args.with_reference)
            result={'provenance':'independent_offline_decode','metrics':run.metrics,'telemetry':run.telemetry,'metadata':run.metadata}
            if args.output:
                from .storage import save_run
                result['manifest']=str(save_run(run,args.output))
        elif args.command=="train":
            from .diagnostics import train_demo_model
            result=train_demo_model(args.output,sessions=args.sessions,windows_per_class=args.windows_per_class,seed=args.seed)
        elif args.command=="demo":
            from .simulation import SimulationConfig,run_simulation
            from .storage import save_run
            settings={"healthy":{},"frequency_offset":{"cfo_hz":1800},"low_snr":{"snr_db":2,"clip_level":4},"clipping":{"gain":4.2},"mixed":{"snr_db":3,"cfo_hz":2200,"gain":3.2}}
            run=run_simulation(SimulationConfig(seed=args.seed,**settings[args.fault]))
            diagnosis=None
            if args.model:
                from .diagnostics import load_model,diagnose
                diagnosis=diagnose(run.features,load_model(args.model))
            path=save_run(run,args.output,diagnosis)
            result={"provenance":"synthetic","manifest":str(path.resolve()),"metrics":run.metrics,"diagnosis":diagnosis}
        elif args.command=="replay":
            from .storage import load_run
            run=load_run(args.path)
            result={"provenance":run.provenance,"capture_integrity":"sha256 verified","metrics":run.metrics,"scope":"Recorded metrics displayed; receiver not recomputed."}
        elif args.command=="probe":
            from .hardware import probe_bladerf
            result=probe_bladerf()
        else:
            from .hardware import capture_rx
            result=capture_rx(args.output,frequency_hz=args.frequency,sample_rate=args.sample_rate,samples=args.samples,gain_db=args.gain)
        from .storage import json_value
        print(json.dumps(json_value(result),indent=2,allow_nan=False))
        return exit_code
    except (ValueError,FileNotFoundError,RuntimeError,OSError) as exc:
        parser.exit(2,f"RF Link Lab: {exc}\n")

