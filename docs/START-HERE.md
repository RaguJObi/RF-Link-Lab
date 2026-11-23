# Start here: understand RF Link Lab

**Your project is a small radio validation lab.** Real Nucleo UART packets and bladeRF TX/RX have already been recorded. A separate receiver reprocessing of one retained capture recovers all twelve offered frames; fresh execution of the new package remains pending.

For the current development path, start with [Hardware evidence & AI](HARDWARE-AI.md): inspect the saved physical failures, distinguish observations from hypotheses, record operator labels and see why one bench session cannot qualify a learned model. No hardware is opened by evidence review.

The software exercises below remain optional. They describe a controlled teaching model; they are not the source of the physical evidence or hardware AI validation.

## The job scenario

A customer says: "Our telemetry packets started failing after a change." Your task is to establish where the data stopped being trustworthy, reproduce the failure, identify the next useful measurement, and save evidence another engineer can rerun. That is the thread connecting embedded firmware, DSP, test automation, and AI in this project.

## Run the first experiment

Follow the installation commands in the [project README](../README.md), then run from the project directory:

```text
python -m rf_link_lab gui
```

Start with the healthy preset. Look at the recovered packet and the I/Q constellation. Change exactly one impairment, rerun, and compare the resulting evidence. A preset is an injected condition whose ground truth is known to the simulator; a diagnosis on an unknown recording is a hypothesis.

For reproducible command line output:

```text
python -m rf_link_lab demo --output artifacts/demo
python -m rf_link_lab train --output artifacts/model
```

The first command produces simulation evidence; the second produces an initial model evaluation on synthetic data. Read the saved result and its limitations before interpreting the score. Neither command opens an SDR or serial port.

## Follow one packet

1. **Telemetry:** sequence number, uptime in milliseconds, and button state form a 17-byte frame with a CRC. Today these values are generated in software. Later the NUCLEO will generate them.
2. **Modulation:** the software assigns the bits to QPSK symbols. QPSK carries two bits per symbol. Pulse shaping turns the symbol sequence into regularly sampled I/Q values.
3. **Channel and receiver input:** noise and frequency offset change those samples; clipping limits their I/Q amplitudes at the simulated ADC rails. These conditions are deliberately modelled.
4. **Receiver:** packet detection, frequency/phase estimation, and decisions attempt to recover the payload. A CRC says whether a candidate packet is internally consistent.
5. **Measurements:** compare recovered bits with the known transmitted reference. Inspect constellation error, packet outcome, and impairment estimates together.
6. **AI:** a model maps measured features to a likely impairment class. Keep its estimate separate from known simulator labels and from proven physical causes.

The [architecture](ARCHITECTURE.md) shows the interfaces. The [learning path](LEARNING-PATH.md) starts with three small experiments and worked answers. Use [hardware day](HARDWARE-DAY.md) when the boards are physically available.

## What makes the portfolio useful

The useful story is: "I implemented a packet format and radio reference, reproduced failures, measured receiver behavior, and tested whether a model adds value to simple diagnostic rules." Save one good example and one failed example with the configuration, software version, and explanation of the next measurement. Later replace the synthetic examples with carefully labelled hardware sessions.

An attractive dashboard alone is insufficient evidence of an RF link. A high synthetic classifier score alone is insufficient evidence of real fault diagnosis. The project becomes stronger as those boundaries are tested and documented.

## First learning checkpoint

Before moving to AI, explain aloud:

- What information is in one telemetry packet?
- How is a QPSK symbol different from an I/Q sample?
- Which condition did you change, and which observation changed?
- What result would make your first explanation wrong?

**Learning status: Not started / not assessed.** Reading files and running a demo are starting activities; demonstrated understanding is recorded after you explain and reproduce an experiment. Nothing in this repository marks you as interview ready or proficient automatically.
