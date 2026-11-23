# Validation record — v0.4.0 hardware evidence and AI preparation

Updated **21 October 2026 (Asia/Calcutta)**. No hardware or Ubuntu connection is available. Development and the checks in this section read local files only; fresh package execution, RF measurements and target GUI validation are deferred.

## v0.4.0 source and saved-evidence checks

SOURCE_TEST_RESULT_PENDING

- Independent review covered capture/session leakage, imported-model qualification, raw ADC feature definitions, missing data, acquisition with zero CRC-valid frames, validation-only acceptance selection and rules comparisons.
- The reproducible `scripts/review_saved_hardware.py` check verifies the retained physical evidence without running the receiver or opening devices. Five case entries contain three distinct raw I/Q hashes from one declared bench session; every label is unknown and training selection is false. Originals remain unchanged.
- Each available original finite capture contains 3,000,000 SC16 Q11 samples. The full-file endpoint check finds zero samples with I or Q exactly at −2048/+2047; out-of-range samples and partial words are checked separately. This is a digital endpoint measurement, not a calibrated analog clipping assessment.
- The selected saved-capture review preserves original incomplete payload (4/12) and selected reprocessed complete payload (12/12). Casebook audit succeeds. Training is blocked with a readiness report only; no hardware model, hardware AI score or fabricated dataset is produced.
- The new native panel has been exercised with actual saved-file review, casebook loading, blocked readiness and unknown prediction. Three offscreen screenshots were inspected. This establishes local Qt rendering and file actions, not Ubuntu foreground usability.
- Review export tests check bytes, HTML rendering consistency and escaped user text. CLI checks keep blocked training distinguishable (exit 3), revalidate source evidence before prediction and do not open a device.
- The v0.4.0 Ubuntu archive includes the new modules/docs and matched private Nuand tools. Its release validation records hashes, source/resource matching and archive modes. Target execution remains pending. v0.3.2 archives/update kits remain retained; the optional Windows executable remains v0.3.0.

The [hardware AI guide](docs/HARDWARE-AI.md) defines inputs, labels, split requirements and limitations. The initial gate of twelve independent bench sessions is a minimum for an evaluation, not proof of generalization.

# Historical v0.3.2 preparation and earlier evidence

Updated 21 October 2026 (Asia/Calcutta). Ubuntu v0.3.2 has been built locally.
The user currently has no connected hardware or Ubuntu access, so deployment,
execution of that new package on Ubuntu and a fresh physical run remain pending.
No new SSH, board or RF operation is attempted in this offline preparation.
The physical results below used v0.3.1 with targeted host/controller patches. The optional Windows package remains v0.3.0. Keep original capture
results separate from later saved-I/Q reprocessing and from a fresh hardware run.

The final source suite for this v0.3.2 preparation passed **350 tests and 114
subtests, with one skip**, in 63.97 seconds; JUnit evidence is
`build/hardware-tests.xml`. These source checks are separate from new-package
execution and the planned fresh UART-to-RF run.

Hardware patch update, 20 October 2026: v0.3.1 extends supported overall TX gain
through 66 dB and verifies RX gain after actual RX startup, before TX. The
focused device-free controller/plan checks passed 70 tests and 107 subtests.
No physical result is implied. The records below describe the v0.3.0 baseline;
its Windows ZIP remains v0.3.0. The new Ubuntu hardware transfer is recorded
separately in `release/UBUNTU-HARDWARE-VALIDATION-v0.3.1.json`. The final focused
v0.3.1 source/command checks passed 81 tests plus 107 subtests (188 JUnit cases),
with zero failures, errors or skips. These checks did not operate hardware.

The historical v0.3.0 records below were updated 19 October 2026. Software and
firmware builds were executed on Windows with Python 3.12.14. The current
v0.3.1 Ubuntu host observation is recorded next and does not retroactively
establish physical results for those earlier checks.

## Actual Ubuntu host inspection — 20 October 2026

- SCP transfer and installation completed at `/home/rfll/SDR/bladeRF/RF-Link-Lab` on `rfll@ubuntu-bench.local`, hostname `ubuntu-bench`. All transferred kit checksums passed. Source/history and the portable runtime were retained.
- Observed target: Ubuntu 22.04.5 LTS x86_64, kernel 6.8.0-136-generic. The portable v0.3.1 `doctor` and `serial-ports` commands executed there; runtime Python was 3.12.15 and the included firmware SHA256 matched the value below. This confirms target CLI execution; native GUI execution remains unverified.
- Existing `/usr/bin/bladeRF-cli` and `/usr/bin/stdbuf` were found. `st-info`/`st-flash` were absent and user `rfll` was not in `dialout` during this inspection.
- Neither Nucleo nor bladeRF appeared in that USB listing. No ST-LINK virtual COM endpoint appeared; serial enumeration listed built-in `ttyS` endpoints only. The bladeRF identity command reported `available: false`, `error_kind: no_device`. No board was flashed and no RF transmission started.
- Evidence: local `release/transfer-v0.3.1-20261020/transfer-status.json` and `ubuntu-preflight.txt`; remote `/home/rfll/SDR/bladeRF/RF-Link-Lab/artifacts/bench/host-preflight-20261020-120550/host-preflight.txt`. The release validation JSON's `target_execution` field is the earlier build-time status; this later host log records actual execution.
- The user reports SMA cables, two separate 50-ohm terminators and pads marked `2211 MCL BW S30W20+` (30 dB, 20 W, DC-18 GHz) and `2205 MCL BW S40W20+ 20W` (40 dB, 20 W, DC-18 GHz). This establishes the reported component identities, not actual fitted connections or measured attenuation/output power.

Those were initial host observations. Subsequent Nucleo, identity, RX-only and
finite TX/RX results are recorded below. The user requested no software loopback
or rehearsal actions.

## Actual Nucleo flash and UART session — 20 October 2026

- Installed `stlink-tools` / `libstlink1` 1.7.0 and confirmed the normal measurement user had fresh `dialout` membership. The USB tree enumerated ST-LINK/V2.1; exactly one F446 target was identified, chip ID `0x0421`, ST-LINK serial `YOUR_STLINK_ID`.
- Read the complete **524,288-byte (512 KiB)** existing STM32 flash before programming. Backup SHA256: `529758c1050b6191383de61f585e317faf784bc7b24c3546cfd8d2d5a97ca86d`. ST-LINK reported the supplied 1,352-byte firmware written and verified; its input SHA256 was `636d701ff7140d075b9d69d265504bf9cf352a393bb4b4b8d8e8a8bb92e0cdf2`.
- The stable virtual COM path was `/dev/serial/by-id/usb-STMicroelectronics_STM32_STLink_YOUR_STLINK_ID-if02`. A **30-second, 115200-baud** physical UART recording completed with **322 packets / 5,474 bytes**; sequence and uptime advanced and decoded button states included both `false` and `true`.
- Observed CRC errors, framing errors, discarded bytes, sequence gaps, duplicates, out-of-order packets and stream errors were all **zero** for this recording. Raw bytes, parsed rows, chunks and manifest were preserved. This establishes this finite host UART observation, not authenticated board identity or RF transfer.
- `link-prepare` selected **12 real recorded UART rows** into the finite payload and completed without opening radio hardware. It did not transmit. The bladeRF probe in this session still reported `available: false`, `error_kind: no_device`; no RF capture or TX began.
- Evidence: local `build/hardware-start-20261020-064706/ubuntu-console.txt`; remote `/home/rfll/SDR/bladeRF/RF-Link-Lab/artifacts/bench/physical-20261020-121721-P68MXp`, including `previous-stm32-flash.bin`, hashes, `console.log`, `uart/`, `uart-observation.json`, `tx/` and `status.json`. The helper ended `uart_recorded_rf_pending` with exit code 0; the packet counters and fields provide the UART evidence beyond that exit status.
- Source-host and Ubuntu clock readings differ by approximately **5 h 30 min**. Preserve each log's recorded time; cross-host UTC alignment and end-to-end latency are not qualified. No clock was changed by this procedure.

The Nucleo is already programmed and recorded; repeating the flash procedure is
not needed to continue. LED appearance and native GUI behavior remain unobserved.

## Later bladeRF identity and imported evidence review — 20 October 2026

- The updated source commit `e1cf004aa3be7ec9c21ef423f1d16f4ef96243cc` fast-forwarded on Ubuntu. The later USB tree enumerated bladeRF 2.0 micro at **5000M**; the application probe returned **`available: true`**, serial `YOUR_BLADERF_SERIAL`, SuperSpeed, 301 KLE FPGA loaded, FPGA v0.14.0 configured from SPI flash.
- Observed versions: bladeRF-cli 1.8.0, libbladeRF 2.4.1, firmware 2.6.0. The installed library warns that firmware is newer than its compatibility-table entries. Identity completed successfully; this warning alone establishes neither a streaming fault nor streaming compatibility. No board firmware or FPGA image was changed by this review.
- Evidence was exported with SHA256 `1b8f54ba452342c5bb22219d85c04d2de45522c4e78bdec3094fee6642c3b7a4` (26 archive entries) and imported to `artifacts/bench/physical-20261020-121721-P68MXp-imported`. Independent local checks verified the export hash, all UART manifest file sizes/hashes, the complete 524,288-byte flash-backup hash, and all **322 raw 17-byte frames** using header/CRC/field inspection. Every sequence/uptime/button field matched its saved packet row.
- Actual raw sequence **32–353** is contiguous; uptime **3200–33100 ms** is nondecreasing. Both button states and **22 decoded transitions** occur. These checks examine saved hardware-session bytes; no synthetic input, serial port, radio or RF receiver was opened.
- A portable UART-only report is at `artifacts/bench/physical-uart-20261020-report/report.html`. Existing `BenchSession` attachment/finalization and `verify_bench_session` verified **six assets, one UART evidence item**, manifest/report consistency and the preserved UART hashes. The latest saved bladeRF probe is submitted as identity context. Checks were entered by Codex from saved logs and files, not observed directly during report generation. No I/Q or RF performance evidence is attached.
- Review evidence: `build/physical-review-20261020/ubuntu-review.txt` and `import-verification.json`. Initial unavailable probes above remain historical observations; the later identity result is available. This identity-only review did not qualify streaming; subsequent captures are recorded next.

## First real RX and finite TX/RX attempts — 20 October 2026

- RX-only capture completed with **1,000,000 complex samples / 4,000,000 bytes** at 1 MS/s, requested 915,000,000 Hz and read back 914,999,998 Hz. Its raw SHA256 `a113525f61681a9009e1c19160fd4ca0755c56c7a745b44badbe19c3b44edfad` and sidecar size/hash were independently checked after import. No RX error was logged. Requested gain was 0 dB; the pre-stream 60 dB readback was explicitly stale/unqualified. Bandwidth was logged at 18 MHz rather than selected. The original metadata, including its static build-time `adapter_validation` wording, is unchanged.
- The user explicitly confirmed the full TX1 → 30 dB → 40 dB → RX1 chain, separate 50-ohm TX2/RX2 loads and no antenna/external amplifier. The applicable saved plan asserts minimum losses 29 + 38.5 = 67.5 dB, zero credited cable loss and an **unmeasured +30 dBm engineering planning envelope**, not a calibrated output reading or guaranteed vendor maximum. Plan declarations remain operator assertions.
- Attempt 1 failed **before TX** because installed bladeRF-cli 1.8.0 rejected `set bitmode 16`. The finalized record has `tx_start_requested: false`, zero RX bytes and confirmed cleanup with both tasks idle. Its saved experiment and 14-asset bench contracts verified. After a targeted compatibility correction, a separate output directory retained attempt 2.
- Attempt 2 completed the finite transport with **TX requested, 3,000,000 RX samples / 12,000,000 bytes and cleanup confirmed**. SC16 Q11 task format, TX gain 50, RX gain 40 after active RX/initial bytes, bandwidth 200 kHz and sample rate 1 MS/s were verified. Both frequencies read back 914,999,998 Hz (−2 Hz from request, within the controller's recorded 10 Hz tolerance). RX SHA256: `2b5ca206a384ea0aa593e30777ff0a282d333bab189df04a4df32bc440d806fe`.
- The saved receiver result is **`acquisition_failed`: 0/12 matched frames, 12 missing, 0 compared bits, 1,632 erased bits, BER unavailable (`null`)**. This is not zero BER and does not establish recovered RF telemetry. Experiment status is `capture_without_complete_payload`, separate from transport status `complete`. Later saved-I/Q diagnosis identified the incompatible host/FPGA held-sample defect described below; this original failed receiver result is retained.
- Imported evidence: `artifacts/bench/physical-rx-20261020-imported`, `artifacts/bench/physical-rf-20261020-attempt1-imported/rf-run` and `artifacts/bench/physical-rf-20261020-attempt2-imported/rf-run-2`. Attempt 2's existing experiment and **18-asset / four-evidence** bench contracts verified without opening hardware or running the receiver. Independent verification JSON files are under `build/physical-review-20261020/`.

## Matched stack, TX buffering and carrier diagnosis — 20 October 2026

- The old stream's full byte count concealed repeated content: independent checks over 488 complete 2048-sample blocks of the RX-only file found the last 1536 samples held constant in **100%** of blocks. The matched-stack RX-only file has a held-tail fraction of **0%** under the same check. Original files and sidecars were retained; no old capture was relabelled as corrected.
- Nuand source tag 2025.10, commit `fcf9423325ae49f5fdd2e5b52ab68bfc2576ad47`, was built on the Ubuntu target into a private prefix. CLI 1.10.0 / libbladeRF 2.6.0 operated with the existing firmware 2.6.0 and xA9 FPGA **0.16.0 loaded by USB into volatile RAM**. The older FPGA 0.14 SPI image was not rewritten. Version output, licenses and build identity were preserved with the cache.
- Matched-stack attempt 3 completed transport and cleanup and recovered **5/12 CRC-valid frames**, sequences 342–346, each matching the offered physical UART fields. Seven frames were missing, with 644 errors / 1632 aligned compared bits, about 78.3% reference EVM and sync score about 0.973. Its received waveform ended early. Its saved experiment and 18-asset bench contracts passed; this was partial recovery, not a complete link.
- Attempt 4 changed the finite TX buffer from 8192 to **32768 samples**, keeping the offered payload and RF gains/settings. Diagnosis found the full waveform present. The **original production receiver result** remains **4/12 frames, 150 errors / 1632 compared bits (about 9.19% BER), reference EVM about 60.7%, CFO estimate +8.546 Hz**, `known_payload_complete: false`. Cleanup is confirmed. RX SHA256: `76291d35a9e46f6bb33482e785db0ea97971073bb896af66e26f467f4df98a7d`.
- The new reference-independent decision-directed type-II QPSK PLL reprocesses that **same real attempt-4 capture** to **12/12 exact CRC-valid frames, 0 errors / 1632 compared bits, zero missing/unexpected/duplicate frames**. Reference-free decoding recovers identical telemetry. The coarse CFO estimate remains about +8.546 Hz; the loop tracks residual phase. Reference EVM is about **35.03%**, a normalized receiver metric rather than calibrated RF quality. The separate `rf-run-4-carrier-tracking-reprocess/reprocessing.json` preserves the original 4/12 metrics and source hashes and records `new_rf_transmission: false`, `original_files_unchanged: true`. The [new report](artifacts/bench/physical-rf-20261020-buffer32768-imported/rf-run-4-carrier-tracking-reprocess/receiver/report.html) is saved separately; the original experiment was not rewritten into a success.
- Evidence: `artifacts/bench/physical-rf-20261020-matched-imported/rf-run-3`, `artifacts/bench/physical-rf-20261020-buffer32768-imported/rf-run-4`, and `build/physical-review-20261020/matched-import-verification.json`. No new synthetic experiment is needed to investigate these actual saved captures.

Current resume point: keep the local v0.3.2 package and update kit ready while
Ubuntu and hardware access are unavailable. When they return, follow
[Ready for hardware](docs/READY-FOR-HARDWARE.md) to update the existing project
and execute the new package. A fresh `RUN-CONNECTED-BENCH.sh` run records
10 seconds of UART and one finite plan-gated RF snapshot without reflashing;
it must have its own new evidence directory.

## Historical software checks

- The final Python suite results and exact counts are recorded in `release/VALIDATION-SUMMARY.json`. One Windows symlink-privilege test is skipped. Coverage includes telemetry CRC/framing, QPSK, independent capture decoding, missing references, acquisition failures, rate conversion, persistent serial lifecycle/counters/provenance, hardware adapter mocks, saved capture integrity, native Qt controls and safe deployment-script stubs.
- Actual Qt widgets rendered offscreen and inspected. Source self-test with `--with-ui` passes **14 checks**, including the finite known-payload rehearsal, portable link experiment and native finite-link controls. It opens no hardware. Standalone panel tests also exit cleanly after joining finished native workers and processing deferred widget deletion.
- The default synthetic modem recovers 12/12 CRC-valid frames, zero errors across 1,632 compared bits, with reference EVM around 5.6%.
- Stored I/Q can be reprocessed without regenerating transmission. Missing payload references leave benchmark BER/reference EVM unavailable. Empty, short, noise-only and failed-acquisition captures have explicit non-success statuses.
- Model generation attempts 240 synthetic inputs; 239 provide complete receiver measurements. One failed acquisition is logged in the held-out test split. Both model and rules score macro F1 1.00 on 47 acquired test observations. Measurement coverage 239/240 is reported separately. This is not 100% end-to-end success or a learned advantage.
- Serial tests cover fragmented reads, CRC recovery, disconnect/reconnect, bounded stop/history, uint32 wrap, duplicates/gaps/reordering and source/session separation. No actual serial port was opened.
- New UART tests preserve junk/corrupt raw bytes and all valid packets beyond display history. Lifecycle replay retains source/session boundaries, connection attempts and stream failures. Byte/event limits, disk errors, short writes and finalization failures become explicit incomplete/error records. Offline replay compares every packet and all counters through the same parser.
- Bench tests cover portable copied evidence after originals are removed, changed files, unsafe paths, escaped HTML, metadata/report consistency, incomplete captures and retry after transient report writes. GUI/CLI tests exercise recording, reconnect boundaries, close-to-finalize, explicit failed opens and interruption. The bundled offline example verifies six valid UART packets, one CRC rejection, one observed gap, one duplicate and one out-of-order arrival, plus a separate three-frame synthetic I/Q run. All physical checks remain unattempted.
- Finite TX tests cover actual SC16Q11 words, rate conversion, zero guards, known-reference hashes, incomplete/unexpected telemetry and scoped BER. A receiver normalization fix rejects zero-energy acquisition windows without treating floating-point residue as a strong preamble. The bundled [conducted rehearsal](artifacts/reference/conducted-rehearsal/report.html) recovers **12/12 known frames, zero errors across 1,632 compared bits and approximately 1.03% reference EVM** from a labelled software channel.
- The Linux finite duplex controller is tested through an in-memory fake CLI: explicit identity, actual settings readback, finite repeat/sample counts, RX bytes before TX, remaining capture budget, acknowledgements, stream errors, partial files, cancellation/Ctrl-C and bounded cleanup. The mock integration through the actual controller and independent decoder also recovers 12/12 frames. No Nuand process or hardware was used in these tests.
- Plan tests reject unknown specifications, bad units/types, mismatched device/settings, missing loading declarations, insufficient attenuation/power margins and nonfinite inputs. Overall TX gain defaults to -20 dB within the project's -23 through 0 dB integer subset; this is distinct from the DSA attenuation scale and from RF power in dBm. A separate operator-supplied power upper bound is required.
- Experiment verification cross-checks preserved receiver metrics/outcome and offered TX identity. Failed/cancelled attempts retain partial evidence. Native controls block overlapping radio operations, attach experiment evidence to an active bench, request stop on close and keep the existing synthetic classifier disabled for known-payload link results.
- Ubuntu scripts pass Bash syntax and stubbed execution tests, including space-containing paths, selected-probe flash arguments and wrong-MCU rejection. No privileged installation or flash was executed.

## STM32 firmware

The unchanged v0.2.0 F446RE image is included in v0.3.0. It was compiled with Zig 0.15.2 Clang/LLD for Cortex-M4. Native C codec/invalid-input and debounce/timer-wrap tests passed. ELF memory ranges, all 113 vectors, Intel HEX reconstruction and SHA256 were inspected. A separate rebuild produced identical BIN bytes. This release changes host software, not board firmware.

- BIN size: 1,352 bytes.
- Initial SP: 0x20020000; reset vector: 0x080001c5.
- BIN SHA256: `636d701ff7140d075b9d69d265504bf9cf352a393bb4b4b8d8e8a8bb92e0cdf2`.
- Nominal 16 MHz HSI; USART2 PA2/PA3 AF7 at 115200 8N1; PC13 active-low with 30 ms debounce; PA5 LED.
- **Current status: PHYSICAL_FLASH_AND_UART_OBSERVED.** The original build-time status was `BUILT_NOT_HARDWARE_TESTED`. The physical session above now establishes verified programming, booted telemetry and both decoded button states; visual LED behavior remains unverified.

## Portable packages

- Locally built Ubuntu v0.3.2: the builder verifies the pinned Linux-built Nuand cache, all nine license/notice files, ELF identity and archive modes, includes the three daily/matched-stack helpers, excludes mutable device/linker logs, and emits a separately hashed corresponding-source companion with both pinned submodules. The 22 affected packaging/deployment checks passed using archive fixtures and script checks; no new package or hardware execution is implied. The actual archive was built locally and inspected; Ubuntu target execution remains pending. Use the release validation JSON and checksums for the exact final rebuilt archive, rather than treating source checks as target execution.
- Ubuntu v0.3.0 baseline: assembled from official python-build-standalone Linux CPython plus Linux binary wheels. Input hashes, archive paths, x86_64 ELF identity, launcher executable modes and firmware-copy identity were checked; that baseline had not been executed on Ubuntu when recorded. The current v0.3.1 portable CLI has now executed on the actual Ubuntu target as recorded above.
- Windows: built by PyInstaller. `scripts/verify_windows_bundle.py --archive <zip>` executes the delivered program from a fresh extraction, with development Python paths removed and only Windows System32 on PATH. Consult `release/VALIDATION-SUMMARY.json` for this release's actual fourteen-check execution result and archive hashes. These are software checks on this Windows host. Physical finite duplex is intentionally Linux-only.
- Generated archives are under `release/`, excluded from source Git history. The existing bench completed USB permissions and host dependencies on 20 October. The new package supplies its private Nuand tools and pinned FPGA image; a normal update needs no Nuand compilation or pip. Any genuinely missing operating-system dependency on a different host is a separate setup step.

## Not established

Full known-payload recovery is established for **saved real I/Q reprocessing**,
with the original attempt-4 4/12 result preserved. A fresh v0.3.2 package run from
new UART through RF and decoding, and that package's target execution, remain
pending. These are separate evidence stages.
Calibrated TX output, sensitivity, standards conformance, independent-radio
robustness and hardware AI accuracy remain unverified. Nucleo flashing and the
finite UART/button-state recording are established above; visual LED, native
GUI, cross-host timing and end-to-end latency remain unqualified. The user has
confirmed fitted connections and the saved power plan has an explicitly
unmeasured planning envelope. Software tests do not assess learner understanding
or job readiness.

## Reproduce the historical software checks

The commands below retain the development evidence procedure. For the current
hardware-only session, use [First physical session](docs/HARDWARE-FIRST-RUN.md).

```text
python -m pytest -q
python -m rf_link_lab self-test --with-ui --output artifacts/self-test.json
python -m rf_link_lab train --output artifacts/model
python -m rf_link_lab decode artifacts/reference/healthy --with-reference
python -m rf_link_lab bench-demo --output NEW_SESSION_FOLDER
python -m rf_link_lab bench-verify NEW_SESSION_FOLDER
python -m rf_link_lab uart-replay NEW_SESSION_FOLDER/uart
python -m rf_link_lab link-prepare --output NEW_TX_FOLDER
python -m rf_link_lab link-rehearse NEW_TX_FOLDER --output NEW_EXPERIMENT_FOLDER
python -m rf_link_lab link-verify NEW_EXPERIMENT_FOLDER
python scripts/build_firmware.py
```

Follow [Connect and run](docs/CONNECT-AND-RUN.md) for target setup and [the model card](docs/MODEL-CARD.md) for score interpretation.
