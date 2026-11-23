"""Validate operator declarations before an explicit conducted RF experiment.

This module opens no device and does not measure RF power. Passing its checks
records an operator's asserted power bound and the project's conservative
working policy; it does not authenticate the declarations or certify hardware.
"""

from __future__ import annotations

import math
from typing import Any


PLAN_SCHEMA = "rf-link-lab.conducted-plan.v1"
VALIDATION_SCHEMA = "rf-link-lab.conducted-plan-validation.v1"
ATTENUATOR_POWER_MARGIN_DB = 3.0
MAXIMUM_RX_WORKING_CEILING_DBM = -20.0
MINIMUM_FREQUENCY_HZ = 70_000_000
MAXIMUM_FREQUENCY_HZ = 6_000_000_000
MINIMUM_TX_GAIN_DB = -23
MAXIMUM_TX_GAIN_DB = 66


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object with explicit declarations.")
    return value


def _fields(value: dict[str, Any], name: str, expected: set[str]) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing:
        raise ValueError(f"{name} is missing required fields: {', '.join(sorted(missing))}.")
    if unknown:
        raise ValueError(f"{name} contains unsupported fields: {', '.join(sorted(str(key) for key in unknown))}.")


def _text(value: Any, name: str, maximum: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty text, at most {maximum} characters.")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} must not contain control characters.")
    return value.strip()


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    # JSON booleans must never stand in for a physical value.
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite number from {minimum:g} to {maximum:g}.")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} must be a finite number from {minimum:g} to {maximum:g}.")
    return result


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def _boolean(value: Any, name: str, required: bool) -> bool:
    if type(value) is not bool or value is not required:
        raise ValueError(f"{name} must be explicitly {str(required).lower()}.")
    return value


def _dbm_to_watts(dbm: float) -> float:
    return 10.0 ** ((dbm - 30.0) / 10.0)


def validate_connection_plan(
    plan: dict[str, Any], *, frequency_hz: int, tx_gain_db: int, device_serial: str,
) -> dict[str, Any]:
    """Return recorded declarations/calculations, or reject an incomplete plan.

    The selected device, center frequency and TX gain must exactly match the
    declaration. Gain is a device control in dB, never an output power in dBm.
    The independent ``tx_power_bound.upper_dbm`` is supplied by the operator.

    Every attenuator must support the experiment frequency and its conservative
    input bound with at least 3 dB of margin. The RX upper bound must be at or
    below a declared ceiling no higher than the project's -20 dBm working
    ceiling. These are project restrictions, not device absolute ratings.
    """
    frequency_hz = _integer(frequency_hz, "frequency_hz", MINIMUM_FREQUENCY_HZ, MAXIMUM_FREQUENCY_HZ)
    tx_gain_db = _integer(tx_gain_db, "tx_gain_db", MINIMUM_TX_GAIN_DB, MAXIMUM_TX_GAIN_DB)
    device_serial = _text(device_serial, "device_serial", 128)
    if device_serial.lower() in {"*", "any", "auto", "default", "unknown", "later", "tbd", "unsure"}:
        raise ValueError("device_serial must explicitly identify the selected device; automatic or unknown selection cannot permit live TX.")
    plan = _mapping(plan, "plan")
    _fields(plan, "plan", {
        "schema", "device_serial", "connection", "operator_checked_connections",
        "unused_ports_correctly_loaded", "active_antenna_connected", "cable_loss_db",
        "attenuators", "tx_power_bound", "rx_working_ceiling_dbm",
    })
    if plan.get("schema") != PLAN_SCHEMA:
        raise ValueError(f"plan.schema must be {PLAN_SCHEMA!r}; unspecified plans cannot permit live TX.")
    plan_serial = _text(plan.get("device_serial"), "plan.device_serial", 128)
    if plan_serial != device_serial:
        raise ValueError("plan.device_serial does not match the explicitly selected device_serial.")
    if plan.get("connection") != "TX1_ATTENUATORS_RX1":
        raise ValueError("plan.connection must be TX1_ATTENUATORS_RX1 for the conducted TX1 to RX1 path.")
    _boolean(plan.get("operator_checked_connections"), "plan.operator_checked_connections", True)
    _boolean(plan.get("unused_ports_correctly_loaded"), "plan.unused_ports_correctly_loaded", True)
    _boolean(plan.get("active_antenna_connected"), "plan.active_antenna_connected", False)

    cable_loss_db = _number(plan.get("cable_loss_db"), "plan.cable_loss_db", 0.0, 300.0)
    rx_ceiling_dbm = _number(
        plan.get("rx_working_ceiling_dbm"), "plan.rx_working_ceiling_dbm", -200.0,
        MAXIMUM_RX_WORKING_CEILING_DBM,
    )
    power_bound = _mapping(plan.get("tx_power_bound"), "plan.tx_power_bound")
    _fields(power_bound, "plan.tx_power_bound", {"frequency_hz", "tx_gain_db", "upper_dbm", "source_note"})
    bound_frequency_hz = _integer(
        power_bound.get("frequency_hz"), "plan.tx_power_bound.frequency_hz",
        MINIMUM_FREQUENCY_HZ, MAXIMUM_FREQUENCY_HZ,
    )
    bound_gain_db = _integer(
        power_bound.get("tx_gain_db"), "plan.tx_power_bound.tx_gain_db",
        MINIMUM_TX_GAIN_DB, MAXIMUM_TX_GAIN_DB,
    )
    if bound_frequency_hz != frequency_hz or bound_gain_db != tx_gain_db:
        raise ValueError("The TX power bound frequency_hz and tx_gain_db must match the requested experiment.")
    tx_upper_dbm = _number(power_bound.get("upper_dbm"), "plan.tx_power_bound.upper_dbm", -200.0, 100.0)
    source_note = _text(power_bound.get("source_note"), "plan.tx_power_bound.source_note")
    if source_note.lower().strip(" .!?") in {"", "unknown", "later", "tbd", "unsure", "n/a", "none", "todo", "not known"}:
        raise ValueError("plan.tx_power_bound.source_note must state a known source or measurement basis; a placeholder cannot permit live TX.")

    attenuators = plan.get("attenuators")
    if not isinstance(attenuators, list) or not 1 <= len(attenuators) <= 32:
        raise ValueError("plan.attenuators must list 1 to 32 identified stages, in TX-to-RX order.")
    normalized_stages: list[dict[str, Any]] = []
    stage_calculations: list[dict[str, Any]] = []
    labels: set[str] = set()
    accumulated_attenuation_db = 0.0
    for index, stage in enumerate(attenuators):
        field = f"plan.attenuators[{index}]"
        stage = _mapping(stage, field)
        _fields(stage, field, {"label", "attenuation_db", "frequency_min_hz", "frequency_max_hz", "power_rating_w"})
        label = _text(stage.get("label"), f"{field}.label", 128)
        if label in labels:
            raise ValueError(f"{field}.label must identify a distinct physical stage.")
        labels.add(label)
        attenuation_db = _number(stage.get("attenuation_db"), f"{field}.attenuation_db", 0.0, 300.0)
        low_hz = _integer(stage.get("frequency_min_hz"), f"{field}.frequency_min_hz", 0, 1_000_000_000_000)
        high_hz = _integer(stage.get("frequency_max_hz"), f"{field}.frequency_max_hz", 1, 1_000_000_000_000)
        if not low_hz <= frequency_hz <= high_hz:
            raise ValueError(f"{field} frequency coverage does not include requested frequency_hz.")
        rating_w = _number(stage.get("power_rating_w"), f"{field}.power_rating_w", 1e-15, 1e9)
        # No cable loss reduces a stage's rating requirement: its position is
        # unspecified, so all cable loss is conservatively placed after stages.
        input_upper_dbm = tx_upper_dbm - accumulated_attenuation_db
        input_upper_w = _dbm_to_watts(input_upper_dbm)
        power_margin_db = 10.0 * math.log10(rating_w) + 30.0 - input_upper_dbm
        if power_margin_db + 1e-12 < ATTENUATOR_POWER_MARGIN_DB:
            raise ValueError(
                f"{field} ({label}) has {power_margin_db:.3f} dB of input-power margin; "
                f"project policy requires at least {ATTENUATOR_POWER_MARGIN_DB:g} dB."
            )
        normalized_stages.append({
            "label": label, "attenuation_db": attenuation_db,
            "frequency_min_hz": low_hz, "frequency_max_hz": high_hz,
            "power_rating_w": rating_w,
        })
        stage_calculations.append({
            "label": label, "input_upper_bound_dbm": input_upper_dbm,
            "input_upper_bound_w": input_upper_w, "power_rating_w": rating_w,
            "power_margin_db": power_margin_db,
            "required_margin_db": ATTENUATOR_POWER_MARGIN_DB,
        })
        accumulated_attenuation_db += attenuation_db

    rx_upper_dbm = tx_upper_dbm - accumulated_attenuation_db - cable_loss_db
    if rx_upper_dbm > rx_ceiling_dbm + 1e-12:
        raise ValueError(
            f"RX input upper bound {rx_upper_dbm:g} dBm exceeds declared working ceiling "
            f"{rx_ceiling_dbm:g} dBm; project maximum is {MAXIMUM_RX_WORKING_CEILING_DBM:g} dBm."
        )

    normalized_plan = {
        "schema": PLAN_SCHEMA, "device_serial": plan_serial,
        "connection": "TX1_ATTENUATORS_RX1", "operator_checked_connections": True,
        "unused_ports_correctly_loaded": True, "active_antenna_connected": False,
        "cable_loss_db": cable_loss_db, "attenuators": normalized_stages,
        "tx_power_bound": {
            "upper_dbm": tx_upper_dbm, "frequency_hz": bound_frequency_hz,
            "tx_gain_db": bound_gain_db, "source_note": source_note,
        },
        "rx_working_ceiling_dbm": rx_ceiling_dbm,
    }
    return {
        "schema": VALIDATION_SCHEMA,
        "status": "passed_operator_declarations_and_project_policy",
        "plan": normalized_plan,
        "requested": {"frequency_hz": frequency_hz, "tx_gain_db": tx_gain_db, "device_serial": device_serial},
        "policy": {
            "attenuator_power_margin_db": ATTENUATOR_POWER_MARGIN_DB,
            "maximum_rx_working_ceiling_dbm": MAXIMUM_RX_WORKING_CEILING_DBM,
            "tx_gain_min_db": MINIMUM_TX_GAIN_DB, "tx_gain_max_db": MAXIMUM_TX_GAIN_DB,
            "frequency_min_hz": MINIMUM_FREQUENCY_HZ, "frequency_max_hz": MAXIMUM_FREQUENCY_HZ,
            "cable_loss_reduces_attenuator_rating_requirement": False,
            "description": "Project working restrictions, not hardware absolute ratings.",
        },
        "calculations": {
            "tx_output_upper_bound_dbm": tx_upper_dbm,
            "tx_output_upper_bound_w": _dbm_to_watts(tx_upper_dbm),
            "total_attenuation_db": accumulated_attenuation_db,
            "cable_loss_db": cable_loss_db,
            "rx_input_upper_bound_dbm": rx_upper_dbm,
            "rx_working_ceiling_dbm": rx_ceiling_dbm,
            "rx_headroom_db": rx_ceiling_dbm - rx_upper_dbm,
            "attenuator_stages": stage_calculations,
        },
        "declarations_are_operator_assertions": True,
        "limitations": [
            "The power bound, connection, component ratings and loss are operator assertions; no RF measurement or authentication is performed.",
            "TX gain in dB and waveform amplitude do not establish output power in dBm.",
            "The -20 dBm ceiling and 3 dB component margin are project policy, not bladeRF absolute ratings or a hardware safety certification.",
            "Changing the selected device, frequency, TX gain, waveform power assumptions, cabling or components requires a fresh plan review.",
        ],
    }
