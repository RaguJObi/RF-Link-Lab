# Conducted RF connection and power plan

The v0.3 live experiment requires an explicit connection plan for the selected
bladeRF serial number, frequency and TX gain. Preparation, software rehearsal
and report review do not require this plan and do not authorize transmission.

The user reports a Mini-Circuits BW-S30W20+ marked `2211 MCL BW S30W20+`
(30 dB, 20 W, DC–18 GHz), and a BW-S40W20+ marked
`2205 MCL BW S40W20+ 20W` (40 dB, 20 W, DC–18 GHz), plus SMA cables.
The user has confirmed the fitted TX1 → both stages → RX1 path, for **70 dB
nominal attenuation**, separate 50 Ω terminators on TX2 and RX2, and no antenna
or external amplifier. Those connection declarations remain operator assertions.
The two-port attenuators in the signal chain are separate from the spare-port
terminations.

The primary [BW-S30W20+ datasheet](https://www.minicircuits.com/pdfs/BW-S30W20%2B.pdf)
and [BW-S40W20+ datasheet](https://www.minicircuits.com/pdfs/BW-S40W20%2B.pdf)
specify 50 Ω parts. At 915 MHz, their minimum attenuations are **29 dB** and
**38.5 dB**, respectively, under the datasheets' **+25 °C electrical-specification
conditions**. The resulting conservative minimum cascade loss for those
conditions is **67.5 dB**; the printed 70 dB total is nominal, not a minimum.
Both specify 20 W maximum input at 25 °C with derating at higher ambient
temperature. Apply the specifications to the actual experiment conditions
when completing the plan; these component figures do not establish the fitted
connections or measure the bladeRF output power.

The first physical session's saved `connection-plan.json` selects bladeRF
`YOUR_BLADERF_SERIAL`, 915 MHz, overall TX gain 50, minimum losses
29 + 38.5 dB and zero credited cable loss. It uses an explicitly **unmeasured
+30 dBm engineering planning envelope**, not a measured output or a guaranteed
vendor maximum. Its declared applicable component ratings are conservatively
reduced to 4 W each. The resulting asserted RX bound is −37.5 dBm, with 17.5 dB
headroom below the project's −20 dBm ceiling and about 6.02 dB input-power
margin at the first stage. Preserve the adjacent `plan-basis.json` with that
plan; the calculation does not turn assumptions into calibration.

`RUN-CONNECTED-BENCH.sh` copies this existing plan into each new session, records
fresh UART and starts one finite snapshot burst. It does not flash the Nucleo.
Use the exact [daily command](HARDWARE-FIRST-RUN.md#daily-run-after-installing-the-current-ubuntu-package)
and review the plan again if the device, connections, waveform assumptions or
selected settings change.

## What the check establishes

`validate_connection_plan(plan, *, frequency_hz, tx_gain_db, device_serial)` in
`src/rf_link_lab/conducted_plan.py` opens no hardware. It checks typed operator
declarations and calculates a conservative power budget. An incomplete or
out-of-policy plan raises `ValueError` before a live experiment can proceed.

Passing means **operator declarations and project policy passed**. The check
does not measure RF power, authenticate a component label, verify a cable, or
certify the connected hardware. Preserve this distinction in saved reports.

The fixed project working rules are:

- Use the conducted path **TX1 → listed attenuators, in order → RX1**. The
  operator explicitly checks the connections, correctly loads unused ports and
  declares that no active antenna is connected. Over-the-air paths are outside
  this workflow.
- Select a particular device serial number; wildcard, automatic and unknown
  selection cannot permit live TX.
- Use an integer center frequency from 70 MHz to 6 GHz and an integer TX gain
  from -23 to 66 dB. These are software project restrictions, not a
  statement of the device's full supported settings.
- TX gain here means overall channel gain. The default is -20 dB. Nuand's
  [overall gain and DSA ranges](https://github.com/Nuand/bladeRF/blob/master/fpga_common/include/bladerf2_common.h)
  use different references; the -89.75 to 0 dB DSA range cannot be substituted
  for the overall channel gain. The project's -23 through 66 dB range is an
  integer subset, and the installed device readback must agree. The low default
  is retained; it is not a useful recommended setting for every attenuation
  chain. A reviewed positive gain still requires an independent power bound.
- Supply a separate conservative TX output-power upper bound in **dBm**. Its
  frequency and TX gain must exactly match the requested experiment. It must
  cover the actual waveform, gain configuration and relevant operating
  conditions. Its source or measurement basis must be stated.
- Give every attenuator a distinct label, conservative minimum attenuation,
  frequency coverage and positive applicable power rating. Its input-power
  upper bound must remain at least **3 dB below** that rating.
- Keep the resulting RX input upper bound at or below the declared working
  ceiling. That ceiling may be stricter than, but never higher than, **-20 dBm**.

The **-20 dBm RX ceiling and 3 dB component margin are project policy**. They are
not bladeRF absolute input ratings or a replacement for checking manufacturer
documentation and actual component ratings.

## Exact plan schema

The schema is strict: required fields cannot be omitted, and unsupported fields
are rejected. Additional experiment notes belong outside the plan object. JSON
numbers must be finite; strings such as `"unknown"`, `null`, and booleans cannot
stand in for ratings or measured values. All three connection declarations are
required actual JSON booleans, including the explicit `false` for an antenna.

This is an **incomplete template**, intentionally rejected for live use:

```json
{
  "schema": "rf-link-lab.conducted-plan.v1",
  "device_serial": null,
  "connection": "TX1_ATTENUATORS_RX1",
  "operator_checked_connections": false,
  "unused_ports_correctly_loaded": false,
  "active_antenna_connected": false,
  "cable_loss_db": 0.0,
  "attenuators": [
    {
      "label": "A1",
      "attenuation_db": null,
      "frequency_min_hz": null,
      "frequency_max_hz": null,
      "power_rating_w": null
    }
  ],
  "tx_power_bound": {
    "upper_dbm": null,
    "frequency_hz": 915000000,
    "tx_gain_db": -20,
    "source_note": "later"
  },
  "rx_working_ceiling_dbm": -20.0
}
```

`915000000` and `-20` illustrate field types; they are not a chosen operating
plan or a claim of known output power. The source-note placeholders `unknown`,
`later`, `TBD`, `unsure`, `none`, `n/a` and similar obvious placeholders are
rejected. A detailed but incorrect note can still pass syntax checks; its
technical correctness remains an operator assertion.

Use `attenuators` in physical TX-to-RX order. The UI can describe one stage;
the same schema supports up to 32 stages for a reviewed JSON plan. Frequency
bounds are integers in Hz, inclusive, with minimum no larger than the selected
frequency and maximum no smaller. Check that the actual occupied signal band
and applicable component conditions are also covered when preparing the plan.

`attenuation_db` and `cable_loss_db` mean conservative **minimum** losses for the
conditions of the experiment, accounting for relevant tolerances. A cable loss
of `0.0` is permitted and avoids assuming unverified cable attenuation. Do not
insert a guessed positive loss to make the budget pass. Per-stage attenuation
and cable loss are each limited to 0–300 dB by the JSON validator; ratings must
be between 1e-15 and 1e9 W, and the declared power bound between -200 and 100
dBm. These wide numerical bounds reject invalid or unrepresentable input and
do not establish physically supported device operation.

Check the power rating's meaning and applicability rather than treating a
printed number as universally valid. Include relevant frequency derating,
waveform peak/average conditions, duty cycle and uncertainty when establishing
the conservative bound and applicable ratings. If that information is unknown,
leave the plan incomplete.

## Power calculation and example

TX gain is a device control in **dB**. Output power is a physical quantity in
**dBm**. Neither a TX gain setting nor a normalized digital waveform amplitude
provides an independently established output-power upper bound.

Nuand defines [overall TX gain 60 as approximately 0 dBm](https://www.nuand.com/libbladeRF-doc/v2.6.0/group___f_n___g_a_i_n.html)
and explicitly identifies that relationship as uncalibrated. Its
[micro product specification](https://www.nuand.com/bladerf-2-0-micro/) gives
+8 dBm as typical CW output, not a guaranteed maximum. A planning envelope
chosen with headroom over a typical figure must be recorded as an unmeasured
engineering assumption, rather than a calibrated measurement or vendor limit.
Waveform scaling, overall gain and the fixed attenuators remain separate
inputs to that review.

For a declared upper bound `P_TX` and stage minimum attenuations `A_i`:

```text
P_input_to_stage_i [dBm] = P_TX - sum(A_j for stages before i)
P_input_to_stage_i [W]   = 10 ** ((P_input_to_stage_i - 30) / 10)
stage_margin [dB]        = 10 * log10(stage_rating_W) + 30
                           - P_input_to_stage_i
P_RX_upper [dBm]         = P_TX - sum(A_i) - cable_loss_db
RX_headroom [dB]         = declared_RX_working_ceiling - P_RX_upper
```

Cable placement is not specified in this schema. The stage-rating checks place
all cable loss **after the attenuators**, so it never reduces a stage's rating
requirement. The final RX budget can include the declared minimum total cable
loss. There is no addition of cable loss to attenuator power handling.

As a **fictitious calculation**, take an independently established 20 dBm
upper bound, a 20 dB / 1 W stage, a 30 dB / 0.01 W stage, and zero declared
cable loss. The first stage sees at most 20 dBm = 0.1 W, giving 10 dB of margin.
The second sees at most 0 dBm = 0.001 W, also giving 10 dB of margin. The final
RX bound is -30 dBm, giving 10 dB of headroom below the project ceiling.

Reversing those two stages exposes the 0.01 W stage to 0.1 W. That fails even
though the total attenuation is unchanged. Similarly, a sufficient total
attenuation cannot rescue an overloaded first stage.

These example values describe unit-test fixtures, not this user's hardware.

## Recorded validation result

A passing result has schema `rf-link-lab.conducted-plan-validation.v1` and
status `passed_operator_declarations_and_project_policy`. It contains:

- `plan`: a normalized independent copy of the accepted declarations.
- `requested`: the exact selected serial, frequency and TX gain.
- `policy`: working-ceiling, component-margin and software-range restrictions.
- `calculations`: asserted TX upper bound in dBm and W, ordered per-stage input
  bounds and margins, total attenuation, cable loss, final RX upper bound,
  declared ceiling and remaining headroom.
- `declarations_are_operator_assertions: true` and explicit limitations.

Changing the selected device, frequency, TX gain, waveform power assumptions,
cabling or any component calls for a fresh review. Persist the plan with the
experiment settings and waveform provenance. Do not turn a passing offline
calculation into a claim that transmission happened or that the physical
connections were independently verified.

## Verification boundary

`tests/test_conducted_plan.py` checks the calculations, cascade ordering,
component margins, policy ceilings, frequency coverage, unknown data,
nonfinite values, stale identity/settings and explicit booleans. It exercises
no board, CLI, USB interface or RF port. Actual hardware validation remains a
separate bench-session task.
