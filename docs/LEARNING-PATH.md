# Learning through the project

**Starting state: Not started / not assessed.** The application and tests are learning tools. Their successful execution does not establish your mastery. Work through the first experiment, explain it in your own words, then continue one diagnostic scenario at a time.

## Lesson 1 — How much radio data did we capture?

**Industry situation:** A test engineer receives an I/Q file described as "a few seconds" and must confirm that its size matches the stated settings before investigating a modem failure.

**Intuition:** A sample is one time observation of the complex signal: an I component and a Q component. It is not one telemetry packet or one QPSK symbol. Taking more observations per second does not automatically increase useful payload throughput.

**Units and relationship:** Let `Fs` be complex samples per second (samples/s), `N` the number of complex samples, and `T` duration in seconds. Then `T = N / Fs`. For this single-channel SC16Q11 format, I and Q are each 16 bits, so one complex sample occupies 4 bytes.

**Worked example:** `Fs = 2,000,000 samples/s`; `N = 200,000 samples`. Then `T = 200,000 / 2,000,000 = 0.1 s`. The raw file needs `200,000 × 4 = 800,000 bytes`, about 0.763 MiB. USB transfer overhead and the JSON sidecar add other bytes; they do not change raw sample count.

**Try it:** Predict the duration and raw size of 1,000,000 complex samples at 2 million samples/s. Then inspect the constants and resulting artifact sizes. Use the hardware loader only with a real SC16Q11 file; simulation artifacts may use another container.

**Answer:** 0.5 seconds and 4,000,000 bytes, approximately 3.815 MiB. "Mega" samples/s uses a decimal million, while MiB uses 1,048,576 bytes.

**Common mistake:** Treating the two I/Q components as two different time samples and dividing duration by two. A raw file also does not tell you its sampling rate without metadata.

**Checkpoint:** Draw one I/Q sample, one symbol, and one packet, and explain their different roles. Repeat the calculation tomorrow without the formula visible.

## Lesson 2 — Where did a bad telemetry packet become bad?

**Industry situation:** A dashboard misses a button event. Before blaming RF, verify the MCU produced a valid message and the host saw it.

**Intuition:** A sequence number tells you packet order. Uptime tells you when the MCU thinks the event occurred. A CRC checks accidental corruption of a candidate packet. These observations answer different questions.

**Units:** Uptime is milliseconds; `1000 ms = 1 s`. Sequence and button values are dimensionless integers. CRC is a checksum, not a physical measurement or security credential.

**Worked example:** You receive valid sequence numbers `100, 101, 103`. One number, `102`, is missing. That observation is a gap at this observation point. It does not prove that RF lost it: the MCU, UART queue, host parser, radio path, or logger could be involved. The next useful measurement is the log immediately upstream of the last confirmed boundary.

**Try it:** Use the protocol encoder/parser to split one frame across three byte chunks. Then flip one payload bit in another frame and append a valid frame. Predict which messages are accepted and where recovery occurs.

**Answer:** Chunking alone should not damage a frame; the incremental parser buffers it. The changed frame fails CRC. The following intact frame should still be found. If it is not, inspect framing and parser state before changing radio gain.

**Common mistake:** Counting every rejected byte or candidate as a lost packet. A stream parser's rejection count has a different denominator from packet error rate.

**Checkpoint:** Explain the last confirmed observation and choose one next measurement. Only then form a cause hypothesis.

## Lesson 3 — Why did a clean constellation become a rotating cloud?

**Industry situation:** A receiver works with stored samples but fails when clocks differ. You need to separate a frequency problem from low signal-to-noise ratio.

**Intuition:** A constant carrier-frequency offset steadily rotates the I/Q phase over time. Noise adds random displacement. Increasing RX gain does not generally fix a phase rotation.

**Units and relationship:** Frequency offset `Δf` is in hertz (cycles/s). Over time `t` seconds, extra phase is `2π Δf t` radians. Symbol rate is symbols/s; sample rate is complex samples/s. They are connected by samples per symbol but are not interchangeable.

**Worked example:** With `Δf = 100 Hz`, after `t = 1 ms = 0.001 s`, phase changes by `2π × 100 × 0.001 = 0.628 rad = 36°`. A receiver correction can remove much of that rotation, so state whether a displayed constellation/EVM is before or after correction.

**Try it:** Compare healthy and frequency-offset presets while keeping the other controls fixed. Predict the raw and corrected plots before running the receiver. Then compare a noise-only run.

**Answer:** In the model, frequency offset creates coherent rotation; noise produces scatter. A corrected constellation can look much better than the incoming signal. A receiver failure under a large offset can also invalidate downstream error metrics; inspect synchronization status first.

**Common mistake:** Calling every scattered plot "bad SNR" or reporting corrected EVM as raw front-end quality. Different physical faults can produce similar features.

**Checkpoint:** Explain one observation that supports frequency offset and one observation that would weaken that hypothesis.

## Lesson 4 — Measure rather than decorate

Start from a known reference packet. Define the population before reporting a percentage: payload bits compared, packets attempted, and packets accepted. Distinguish a detection failure from a detected packet with CRC failure. Inspect reference alignment before interpreting BER/EVM. State the receiver corrections and the assumptions of every metric. Save both successful and failed cases.

Read plot units explicitly. The current spectrum is in dB relative to unit complex power, independent of the selected simulated ADC rail. It is neither dBFS nor dBm, and its spectral-power scaling is not a density in dB/Hz. A higher trace alone does not establish received power at an RF connector.

Evidence to produce: a short experiment varying only one parameter, its configuration/result files, a plot, and a paragraph explaining what is supported and what remains unresolved. Synthetic measurements establish model behavior; calibrated RF claims require additional hardware methodology.

## Lesson 5 — Build an AI diagnostic experiment

Begin with a question: **Can measured features identify a dominant injected impairment on new synthetic sessions better than simple rules?** Inspect the feature definitions before training. A label such as "frequency offset" must not be silently included in the inputs.

Compare the model with a documented baseline, inspect class confusion, and inspect individual failures. Keep all frames from one capture/session together when dividing data. Otherwise the evaluation may reward recognition of nearly duplicated data. Keep a held-out test set untouched while choosing features or model settings.

Then ask a harder question: **Does the model remain useful on real hardware captures from a later day?** That requires new evidence and may require new features, labels, and training. Synthetic performance does not answer it.

Evidence to produce: dataset provenance, session split, feature list, baseline/model results, confusion matrix, and at least three error explanations. An unknown or mixed fault should be allowed to remain uncertain.

## Lesson 6 — Turn AI into a useful measurement assistant

The current assistant demonstrates a measured-feature diagnosis and one-control follow-up in simulation, including an option to abstain. The advanced extension must earn that usefulness on real measurements. For example: likely frequency offset → inspect estimator and phase progression → repeat with a known reference → compare corrected packet outcome. The assistant should show the supporting observations, not merely name a class.

Before claiming improvement, measure diagnostic accuracy, abstention behavior on unfamiliar data, and the number of additional tests needed to reach a correct explanation. Compare with the same workflow using simple rules. A language model can explain the report later, but it must cite measured values and must not invent hardware state or automatically change RF output.

## Conservative progress record

All lessons initially remain **Not started**. After a first explained experiment use **Practising**. Mark an individual lesson complete only after a mixed practical assessment, a drawn/explained signal path, and delayed recall. Keep the implementation's test results separate from your learning record. No study milestone implies sponsorship eligibility, a job offer, or mastery of an entire RF role.
