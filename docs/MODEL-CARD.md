# Measured-feature RF experiment assistant

This model is a **synthetic teaching prototype**. It has no bladeRF capture data,
no demonstrated hardware accuracy, and no authority to control a transmitter.
Its useful action is to propose one controlled follow-up experiment from receiver
measurements, with a visible reason and an option to abstain.

## What it does

The receiver measures a six-number signature from the received samples. An
80-tree random forest ranks four controlled conditions: healthy, frequency
offset, low SNR, and ADC clipping. The interface explains its evidence, suggests
one next measurement, and can demonstrate a counterfactual in simulation.

Example: high measured frequency offset and poor EVM suggest an offset
hypothesis. The follow-up removes only the injected offset and reruns the same
simulation. Improved EVM and packet CRC support the hypothesis **within this
simulator**. On hardware, enable receiver frequency correction and compare a
fresh recording while holding the other settings fixed. An improvement would
support a frequency-error hypothesis; it would not identify a particular faulty
oscillator or establish its calibration.

The learned model does not need to beat a rule to be useful coursework. The
current four separated teaching conditions are easy: a transparent threshold
baseline may tie or outperform it. Both results are published. The more demanding
future task is to learn from real, overlapping impairments across independent
recording sessions while selecting efficient follow-up measurements.

## Inputs and the evidence boundary

Only these measurements enter the model, in this order:

- `cfo_abs_hz`: absolute carrier frequency offset estimated from the repeated pilot, in Hz.
- `evm_pct`: received symbol error-vector magnitude, in percent.
- `clipping_fraction`: fraction of received samples at an ADC rail, from 0 to 1.
- `snr_est_db`: pilot-fit residual SNR estimate after provisional measured frequency correction, in dB.
- `amplitude_cv`: coefficient of variation of received sample amplitude.
- `spectral_flatness`: geometric-to-arithmetic mean of received-sample spectral power.

The receiver uses a known training sequence and protocol structure. Pilot-fit
SNR and EVM are estimates, not calibrated RF measurements. Incorrect timing or
poor pilot acquisition can corrupt these measurements. A large estimated offset
at low estimated SNR is particularly ambiguous.

Simulator ground-truth CFO, injected SNR, class labels, gain setting, session IDs,
seeds, packet metrics, and file names are **excluded from the feature vector**.
They remain in the CSV audit columns so another engineer can reproduce an
experiment and check its label. Seeds create data; they are not model inputs.

## Data generation and splits

The standard training command attempts 20 virtual sessions, four classes per
session, three windows per class, and six telemetry frames per window: 240
inputs in total. The v0.2 seed-2026 run produces 239 complete measured records;
one failed acquisition is audited separately rather than imputed. Seed 2026
makes the data reproducible. Twelve sessions train
the forest, four sessions select acceptance settings, and four sessions are held
out for the reported test results. Each split contains all four classes. A window
from one session can never cross into another split.

Every class in a session shares the same receiver gain (.45–.85), carrier phase
(−180–180 degrees), and timing delay (13–80 samples). Each window shares its
nominal SNR (24–34 dB) and nominal offset (±2 Hz) across classes. Samples use
200 ksample/s, four samples/symbol, and disabled payload frequency correction.
These distributions prevent a label-specific gain, phase, or receiver setting
from becoming a convenient proxy. The single controlled interventions are:

- Frequency offset: absolute injected CFO 800–3,800 Hz.
- Low SNR: injected SNR 0–9 dB.
- Clipping: ADC rail reduced to .12–.30 from a nominal 4, with the same gain distribution.

Nominal rail 4 gives the low-SNR experiment headroom. Strong added noise at a
rail of 1 can also clip, creating a real mixed impairment in the simulator. That
case should not be labelled a pure noise experiment. The injected SNR uses a
fixed reference; receiver signal gain affects the residual SNR estimate.

These virtual sessions are independent random realizations of **one simulator**.
Session grouping prevents adjacent-window leakage; it does not establish
generalization to new radios, environments, waveforms, or real recording days.

The saved `split-manifest.json` records session membership, seeds, counts, label
ranges, nuisance policy, and the feature boundary. `dataset.csv` includes the
measured features and separately named audit metadata. `evaluation.json` is the
source of truth for the actual scores of the generated model.

## Learning and evaluation

The model is scikit-learn `RandomForestClassifier`: 80 trees, maximum depth 9,
minimum two samples per leaf, balanced class weights, fixed seed, and one worker.
The forest is fitted only on the training sessions. No hyperparameter search is
performed against the heldout test sessions.

The fixed rule baseline checks clipping above .035, then absolute CFO above
500 Hz when residual SNR is at least 12 dB, then residual SNR below 12 dB. Its
priority is clipping, frequency offset, low SNR, healthy. Suppressing the offset
signature on a noisy pilot avoids treating every unreliable estimate as a second
physical fault. It can also miss a genuine combined offset-and-noise fault.

Both classifiers use exactly the same test records. Reports include accuracy,
macro F1, a confusion matrix with explicit class order, and per-class precision,
recall, F1 and counts. Classification scores are computed **before abstention**;
selective coverage and accepted accuracy are reported separately. A model that
rejects cases cannot improve its headline score by silently dropping them.

The historical v0.1 seed-2026 reference run scored macro F1 1.00 for both the
forest and the fixed rules on 48 heldout synthetic windows, with 100% acceptance. These
high scores reflect deliberately separated synthetic conditions and a small
test set. They do not demonstrate a learned advantage or hardware accuracy.
The v0.2 results and acquisition coverage are described below. Regenerated
artifacts, not the historical example, determine the current run's results.

## Abstention and model support

### v0.2 receiver measurement coverage

The independent receiver now rejects failed acquisitions instead of manufacturing a feature vector. The seed-2026 reference attempt uses 240 synthetic inputs; 239 have complete measurements and one failed test acquisition is logged in `split-manifest.json`. Both classifiers score macro F1 = 1.00 on 47 acquired test inputs. This is conditional classification performance, not a 100% end-to-end success rate. `receiver_measurement_coverage` and excluded counts accompany scores. Unavailable BER/reference EVM in arbitrary external captures are never imputed for the existing model.

The displayed values are mean learned-tree class support, not calibrated
probabilities of physical root cause. Tree support can be high for an unfamiliar
input. The assistant returns `unknown` when:

1. A feature lies beyond the train-and-validation extrema with 25% slack.
2. More than one reliable fixed-rule impairment signature appears.
3. Top-class support falls below the validation-selected threshold.

The threshold is the first candidate from .55 to .95 in .05 increments with at
least 50% validation coverage and 95% accepted validation accuracy; fallback is
.85. This is a selection rule for a small synthetic development set, not a
statistical guarantee. Feature bounds use training and validation only. The
heldout test set selects neither threshold nor bounds.

Twelve separate synthetic probes combine offset, noise and clipping. Their
rejection fraction is reported as a small demonstration, not an out-of-
distribution benchmark. Other unfamiliar channels, mixed conditions, or invalid
receiver estimates can still receive a confident-looking incorrect label.

## Portable model format

`model.json` contains learned tree arrays, feature/class order, feature importance,
training-session IDs, and acceptance settings. Inference traverses these arrays
with NumPy. No executable pickle or joblib is loaded. A parity check compares
JSON inference with scikit-learn on all test vectors and records the maximum
absolute probability difference. The loader rejects unsupported formats and
invalid branch/leaf arrays. Model files still need ordinary integrity and size
checks if later accepted from arbitrary external sources.

## One-control intervention

`propose_followup` returns the full proposed simulation settings, the single
changed field, a rationale, an expected observation, and `simulation_only=true`.

- Offset: set injected `cfo_hz` to zero.
- Noise: set injected `snr_db` to 30 dB.
- Clipping: reduce `gain` relative to the selected ADC rail.
- Healthy or unknown: repeat with a fresh random seed before interpreting stability.

The counterfactual manipulates known simulator ground truth to teach causality.
It is separate from the measured-feature classifier and is labelled accordingly.
It is not evidence that an actual instrument has performed the suggested action.

## Hardware and future AI milestones

Before retraining, collect real bladeRF recordings with recorded sample rate,
gain, bandwidth, cable/attenuator setup, device/firmware versions and verified
impairment labels. Keep entire acquisition sessions out of training. Preserve a
new untouched test set when a previous test result influences implementation.
Start by comparing healthy captures with the synthetic feature envelope; record
domain mismatch before claiming transfer.

Useful later work includes mild overlapping faults, reliable acquisition-failure
labels, calibrated uncertainty, waveform features, hardware-session holdouts,
and an experiment-selection policy scored by how many measurements it needs to
isolate an injected fault. An optional language model can explain measured
evidence, but must not invent RF readings or override unknown decisions.
