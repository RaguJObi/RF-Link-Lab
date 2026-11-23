# Hardware evidence and AI

The v0.4 workflow turns saved physical RF experiments into a traceable
troubleshooting case library. You can review evidence, annotate a diagnosis,
check whether enough independent sessions exist for learning, and later train
and evaluate a hardware model. These actions work on saved files and open no
radio, serial port or network connection.

**Status on 21 October 2026:** the boards and Ubuntu desktop are unavailable.
The preserved collection contains **five processing variants from four
physical attempts**, **three distinct raw RX recordings**, and **one operator
bench session**. One attempt stopped before TX and has no raw recording. Every
case is labelled **unknown** and is unselected for training. **There is no
trained or scored hardware model today.** A successful saved-capture decode
does not add another physical measurement or an independent training example.

## What this teaches for an RF or embedded role

- **Bring-up and integration:** separate a host/firmware/FPGA data-format
  problem from a weak RF signal. The earlier repeated-sample pattern is an
  example of transport evidence that must be resolved before interpreting
  RF features.
- **Receiver debugging:** compare two receiver versions against the same
  original recording. The selected carrier-tracking reprocessing recovers
  12/12 messages from a capture whose original result recovered 4/12. This
  supports a processing improvement on those bytes; a fresh RF run remains
  the next measurement.
- **Test and validation:** preserve failed attempts, exact settings, logs,
  checksums and the next measurement. A report should let another engineer
  assess the conclusion without reconstructing the chat.
- **Applied ML:** build labels from measured bench work, hold out whole
  sessions, compare a learned model with explicit rules, and allow an
  uncertain result. An impressive model score needs independent evidence
  behind it.

The useful deliverable for an interview is the chain from observation to
measurement to reproducible result. This stage supplies that structure;
hardware qualification and useful learned diagnosis require more bench work.

## Keep four kinds of statement separate

An **observation** describes preserved bytes or readbacks: a version pair,
held-sample pattern, raw ADC endpoint count, or selected payload comparison.
A **hypothesis** proposes an explanation and names a next measurement. The
review records each finding's kind, severity and supporting evidence.

An **operator label** is your explicit annotation of a case. New cases default
to `unknown`; a known label requires an evidence note. Training selection is
also explicit and defaults off. A rule, successful decode or model suggestion
never changes the operator label automatically. Annotation revisions preserve
the earlier decisions.

A **learned prediction** can exist only after a hardware-only model has passed
the data gates and been created. It suggests a measured signature, not a failed
component or proven root cause. Learned-tree support is an uncalibrated
decision statistic, not a probability that the hardware has a particular fault.

## Six measured inputs

Only these six reference-independent values enter the hardware model:

- `cfo_abs_hz` — absolute carrier-frequency offset, in **Hz**, estimated using
  the public acquisition sequence.
- `pilot_evm_pct` — normalized error against the public synchronization
  symbols, in **%**. It does not use the private telemetry payload.
- `snr_est_db` — public-pilot mean-to-residual estimate, in **dB**. It is not a
  calibrated RF input SNR measurement.
- `clipping_fraction` — the fraction of **all original complex SC16 Q11
  samples** whose original I or Q component equals an ADC endpoint code:
  **−2048 or +2047**. A sample hitting both endpoints counts once. The review
  scans the complete original raw file with bounded memory; it does not use
  the old receiver proxy based on repeated observed extrema. Codes outside
  the SC16 Q11 range or incomplete sample bytes are recorded as format
  problems. The definition ID is `raw_sc16q11_adc_rail_fraction_v1`.
- `amplitude_cv` — amplitude standard deviation divided by mean, **unitless**,
  over the saved post-synchronization receiver window, including its trailing
  samples.
- `spectral_flatness` — geometric-to-arithmetic mean of Welch-bin power,
  **unitless**, for the saved selected baseband capture window.

The public synchronization sequence is part of the receiver protocol. Using it
does not give the model the private expected payload. **BER, reference EVM,
packet counts, known-payload success, filenames, session IDs and operator notes
are not model features.** Comparison metrics remain visible as engineering
evidence beside the separate feature vector.

Capture length and processing matter. Adding idle samples changes the
denominator of the full-capture ADC-rail fraction. Amplitude variation and
spectral flatness also depend on the saved window and receiver processing.
Preserve the acquisition settings and compare like windows before attributing
a feature change to hardware.

The fixed engineering baseline currently screens for frequency offset above
500 Hz with estimated SNR at least 12 dB, estimated SNR below 12 dB, or
ADC-rail fraction above **1e-4 (0.01%)**. These are initial comparison rules,
**not physically qualified fault thresholds**. In particular, the rail
threshold depends on the full recording's idle time. A baseline label of
`healthy` means none of these screens fired; it does not certify the link.

## Cases, integrity and independent sessions

A case library contains `casebook.json` and immutable saved reviews under
`reviews/`. Each case preserves its original raw RX SHA256, selected receiver
identity, operator bench-session ID and annotation history. Reprocessing the
same raw file keeps the same capture identity. Its variants can be useful for
debugging, but cannot become independent training rows.

The case audit rechecks review hashes and their external original source
files. **Keep those originals.** Copying only the case library does not copy
the raw captures. Reviews currently record absolute source paths; after a
transfer, preserve the originals and make new reviews/cases against their
verified destination paths rather than editing old hashes. Hashes detect byte
changes; physical origin, session independence and labels remain operator
declarations.

Dataset export explains every exclusion. Eligible learning cases need intact
physical transport evidence, valid acquisition/synchronization, all six finite
features, a supported RF label, and explicit training selection. Missing
measurements stay unavailable and are not replaced with zeros. Pipeline
corruption, absent raw files and incompatible feature definitions are excluded.
**Passing a payload CRC or recovering every known message is not an eligibility
gate**: a synchronized case with no valid frames can still supply RF features.
This avoids defining the learning set only through successful messages.

The RF model labels are `healthy`, `frequency_offset`, `low_snr` and `clipping`.
`transport_fault`, `receiver_processing` and `other` remain useful case labels,
but are excluded from this RF-signature model. Choose at most one processing
variant for each original raw SHA256. Duplicate selected variants are rejected
rather than counted as extra data.

The first training gate requires:

1. At least **12 independent operator bench-session groups** and **two RF
   classes** among eligible cases.
2. A split of **whole sessions** into training, validation and test groups.
   Captures and reprocessing variants from one physical session stay together.
3. At least **two class-bearing sessions for every included class in every
   split**, requiring at least six distinct sessions per class. If the recorded
   class/session pattern cannot satisfy that split, training remains blocked.

Use one honest session ID for related measurements from the same bench
session. Renaming copied files or assigning new IDs to reprocessed variants
does not create independence. Meeting the minimum permits an initial
evaluation; it does not establish broad hardware generalization.

Training uses a fixed small random forest. Its acceptance threshold is selected
on validation sessions; final test sessions are held out for reporting.
Outputs include the dataset, split manifest, model, evaluation and readiness
record. The evaluation compares the model with the fixed rule baseline and
reports accepted accuracy, coverage and abstentions. An inference can return
`unknown` for low support, an unsupported feature envelope, mixed signatures
or an untrained rule signature. A model can be created while its validation
results leave prediction acceptance disabled.

## Use the native workbench

Open **Hardware → 6 · Evidence & AI**:

1. Choose the original physical experiment folder and, optionally, a saved
   reprocessed receiver folder from the same raw recording. Press **Review
   saved evidence**. The original outcome and selected receiver outcome are
   shown separately.
2. Save a review report if useful. This creates a new `review.json`,
   `report.html` and `export.json`; the old experiment is unchanged.
3. Create or load a case library. Enter the actual bench-session ID, leave
   uncertain labels `unknown`, and save the case. Use the case selector to
   append a later label revision with an evidence note.
4. Check library integrity and training readiness before considering training.
   Exported datasets include excluded cases and their reasons.
5. Train only through the explicit action. If blocked, the result explains why
   and no model is created. Selecting a qualified hardware model enables an
   assessment of a reviewed recording; suggestions do not overwrite labels.

Long file checks and training run in the background. Wait for an active file
operation to finish before closing. The panel performs no analysis or device
query merely because the workbench was opened.

## Commands for the current Windows checkout

These commands use the development environment in the project folder. They
read existing hardware evidence and write only the explicitly requested new
outputs. No attached hardware or Ubuntu connection is needed.

~~~powershell
cd "C:\path\to\RF-Link-Lab"
$PY = ".\.venv\Scripts\python.exe"
$EXPERIMENT = "artifacts/bench/physical-rf-20261020-buffer32768-imported/rf-run-4"
$RECEIVER = "artifacts/bench/physical-rf-20261020-buffer32768-imported/rf-run-4-carrier-tracking-reprocess/receiver"
$CASEBOOK = "artifacts/hardware-ai/v040-20261021-final/casebook"

# Review only; prints a result without creating a report.
& $PY -m rf_link_lab hardware-review $EXPERIMENT --receiver-run $RECEIVER

# Inspect the preserved collection and explain current training blockers.
& $PY -m rf_link_lab casebook-audit $CASEBOOK
& $PY -m rf_link_lab hardware-readiness $CASEBOOK
~~~

`hardware-readiness` returns **exit code 3 when blocked**, with reasons in its
JSON result. This is the expected outcome for today's collection, not a
hardware test failure.

The shipped selected review is intended at
`artifacts/hardware-ai/v040-20261021-final/selected-review-final/`. To make your own
report and library, choose fresh output names; report exports refuse to
overwrite an existing folder:

~~~powershell
$WORK = "artifacts/hardware-ai/my-review-20261021-A"
$NEW_CASEBOOK = "$WORK/casebook"
& $PY -m rf_link_lab hardware-review $EXPERIMENT --receiver-run $RECEIVER --output "$WORK/selected-review"
& $PY -m rf_link_lab hardware-review-verify "$WORK/selected-review"
& $PY -m rf_link_lab casebook-new --output $NEW_CASEBOOK
& $PY -m rf_link_lab casebook-add $NEW_CASEBOOK $EXPERIMENT --receiver-run $RECEIVER --session-id "physical-20261020" --notes "Saved physical recording; operator fault diagnosis remains unknown."
& $PY -m rf_link_lab casebook-audit $NEW_CASEBOOK
& $PY -m rf_link_lab casebook-export $NEW_CASEBOOK --output "$WORK/dataset.json"
~~~

`hardware-review --output` is the save operation; there is no separate
`hardware-review-save` command. `hardware-review-verify` checks the exported
bytes and HTML rendering contract. `casebook-audit` separately checks the
original evidence linked from the cases. Neither check authenticates physical
origin.

To regenerate the five-case review collection from the locally retained
originals into a fresh folder, use the dedicated helper:

~~~powershell
& $PY scripts/review_saved_hardware.py --output "artifacts/hardware-ai/my-full-review-20261021-A"
~~~

It preserves all five labels as unknown, checks that the source bytes remain
unchanged, and requires the resulting training request to remain blocked. The
original recordings must already exist locally; the helper does not obtain or
create hardware evidence.

`casebook-add` prints its new `case_id`. Replace the placeholder below with that
value to append an annotation revision. This example keeps the diagnosis
unknown and training selection off:

~~~powershell
& $PY -m rf_link_lab casebook-label $NEW_CASEBOOK CASE_ID_FROM_ADD_RESULT --label unknown --notes "Awaiting an independently supported bench diagnosis."
& $PY -m rf_link_lab hardware-readiness $NEW_CASEBOOK
& $PY -m rf_link_lab hardware-train $NEW_CASEBOOK --output "$WORK/training-attempt-A" --seed 2026
~~~

Known labels require an evidence note. Add `--for-training` to a future
`casebook-add` or `casebook-label` only when you deliberately select a supported
RF-labelled processing variant. Omitting that flag leaves its training
selection false. The current collection should remain unknown until measured
evidence supports an annotation.

`hardware-train` rechecks readiness. When blocked it writes only
`readiness.json`, returns **exit code 3**, and creates no `model.json` or scored
evaluation. Use a new output folder for another training request.

After enough future bench sessions support a model, this is the assessment
command. The model path below is a placeholder, not an existing deliverable:

~~~powershell
& $PY -m rf_link_lab hardware-diagnose $EXPERIMENT --receiver-run $RECEIVER --model "PATH_TO_QUALIFIED_HARDWARE_MODEL/model.json" --output "$WORK/model-assessment"
~~~

The CLI requires `--model` for learned assessment. The native panel can also
show a rules-only assessment without one and states that no learned hardware
model was supplied. Hardware inference accepts the separate validated JSON
model format; it does not load a synthetic-channel classifier.

## What remains for the next bench session

Run the new package on Ubuntu, verify native operation there, and preserve one
fresh finite conducted RF measurement before claiming target readiness. Follow
the wiring and power-plan instructions in [Ready for hardware](READY-FOR-HARDWARE.md)
and [the conducted plan guide](CONDUCTED-PLAN.md).

Then collect independent sessions with measured, deliberately controlled RF
conditions, consistent capture windows and supported operator labels. Retain
transport failures and acquisition failures as troubleshooting cases even
when they are unsuitable for this model. Compare future model evaluation
against the fixed baseline and verify its abstention behavior on unfamiliar
conditions. Hardware threshold qualification, generalization to other radios,
calibrated RF measurements, continuous telemetry and end-to-end latency remain
open. No development or saved-file test in this stage closes those boundaries.
