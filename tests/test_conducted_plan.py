"""Offline checks for operator declarations; these tests never access hardware."""

import copy
import math
import unittest

from rf_link_lab.conducted_plan import validate_connection_plan


def example_plan():
    # Fictitious example values are calculation fixtures, not this user's kit.
    return {
        "schema": "rf-link-lab.conducted-plan.v1",
        "device_serial": "EXAMPLE-SERIAL",
        "connection": "TX1_ATTENUATORS_RX1",
        "operator_checked_connections": True,
        "unused_ports_correctly_loaded": True,
        "active_antenna_connected": False,
        "cable_loss_db": 0.0,
        "attenuators": [{
            "label": "Example 50 dB stage",
            "attenuation_db": 50.0,
            "frequency_min_hz": 1_000_000,
            "frequency_max_hz": 6_000_000_000,
            "power_rating_w": 1.0,
        }],
        "tx_power_bound": {
            "upper_dbm": 20.0,
            "frequency_hz": 915_000_000,
            "tx_gain_db": -20,
            "source_note": "Fictitious conservative bound for an offline unit test only.",
        },
        "rx_working_ceiling_dbm": -20.0,
    }


def validate(plan, **overrides):
    settings = {"frequency_hz": 915_000_000, "tx_gain_db": -20, "device_serial": "EXAMPLE-SERIAL"}
    settings.update(overrides)
    return validate_connection_plan(plan, **settings)


class ConductedPlanTests(unittest.TestCase):
    def test_valid_plan_records_operator_declarations_and_project_scope(self):
        plan = example_plan()
        original = copy.deepcopy(plan)
        result = validate(plan)
        self.assertEqual(plan, original)
        self.assertEqual(result["plan"], plan)
        self.assertEqual(result["status"], "passed_operator_declarations_and_project_policy")
        self.assertTrue(result["declarations_are_operator_assertions"])
        self.assertIn("not hardware absolute ratings", result["policy"]["description"])
        self.assertAlmostEqual(result["calculations"]["tx_output_upper_bound_w"], 0.1)
        self.assertEqual(result["calculations"]["rx_input_upper_bound_dbm"], -30.0)
        self.assertEqual(result["calculations"]["rx_headroom_db"], 10.0)
        self.assertGreater(len(result["limitations"]), 0)

    def test_each_stage_gets_its_own_input_bound_in_tx_to_rx_order(self):
        plan = example_plan()
        first = plan["attenuators"][0]
        first["attenuation_db"] = 20.0
        second = dict(first, label="Second stage", attenuation_db=30.0, power_rating_w=0.01)
        plan["attenuators"].append(second)
        result = validate(plan)
        stages = result["calculations"]["attenuator_stages"]
        self.assertEqual([stage["input_upper_bound_dbm"] for stage in stages], [20.0, 0.0])
        self.assertAlmostEqual(stages[0]["input_upper_bound_w"], 0.1)
        self.assertAlmostEqual(stages[1]["input_upper_bound_w"], 0.001)
        self.assertAlmostEqual(stages[1]["power_margin_db"], 10.0)
        self.assertEqual(result["calculations"]["total_attenuation_db"], 50.0)

    def test_stage_order_changes_rating_requirement(self):
        plan = example_plan()
        first = dict(plan["attenuators"][0], label="First", attenuation_db=20.0)
        second = dict(first, label="Second", attenuation_db=30.0, power_rating_w=0.01)
        plan["attenuators"] = [second, first]
        with self.assertRaisesRegex(ValueError, "input-power margin"):
            validate(plan)

    def test_cable_loss_reduces_rx_bound_but_not_stage_power_requirement(self):
        plan = example_plan()
        plan["cable_loss_db"] = 5.0
        result = validate(plan)
        self.assertEqual(result["calculations"]["rx_input_upper_bound_dbm"], -35.0)
        self.assertEqual(result["calculations"]["attenuator_stages"][0]["input_upper_bound_dbm"], 20.0)
        plan["attenuators"][0]["power_rating_w"] = 0.1
        with self.assertRaisesRegex(ValueError, "input-power margin"):
            validate(plan)

    def test_exact_three_db_component_margin_is_accepted_but_excess_is_not(self):
        plan = example_plan()
        plan["tx_power_bound"]["upper_dbm"] = 27.0
        result = validate(plan)
        self.assertAlmostEqual(result["calculations"]["attenuator_stages"][0]["power_margin_db"], 3.0)
        plan["tx_power_bound"]["upper_dbm"] = 27.001
        with self.assertRaisesRegex(ValueError, "3 dB"):
            validate(plan)

    def test_exact_rx_ceiling_is_accepted_and_excess_is_rejected(self):
        plan = example_plan()
        plan["attenuators"][0]["attenuation_db"] = 40.0
        self.assertEqual(validate(plan)["calculations"]["rx_headroom_db"], 0.0)
        plan["attenuators"][0]["attenuation_db"] = 39.999
        with self.assertRaisesRegex(ValueError, "RX input upper bound"):
            validate(plan)

    def test_operator_can_select_stricter_rx_ceiling_but_cannot_relax_project_ceiling(self):
        plan = example_plan()
        plan["rx_working_ceiling_dbm"] = -30.0
        validate(plan)
        plan["rx_working_ceiling_dbm"] = -31.0
        with self.assertRaisesRegex(ValueError, "RX input upper bound"):
            validate(plan)
        plan["rx_working_ceiling_dbm"] = -19.0
        with self.assertRaisesRegex(ValueError, "rx_working_ceiling_dbm"):
            validate(plan)

    def test_tx_gain_is_not_converted_to_dbm(self):
        plan = example_plan()
        first = validate(plan)["calculations"]
        plan["tx_power_bound"]["tx_gain_db"] = -23
        second = validate(plan, tx_gain_db=-23)["calculations"]
        self.assertEqual(first, second)

    def test_positive_overall_gain_preserves_independent_power_policy(self):
        for gain in [1, 50, 60, 66]:
            plan = example_plan()
            plan["tx_power_bound"]["tx_gain_db"] = gain
            with self.subTest(gain=gain):
                result = validate(plan, tx_gain_db=gain)
                self.assertEqual(result["policy"]["tx_gain_max_db"], 66)
                self.assertEqual(result["calculations"]["rx_input_upper_bound_dbm"], -30)
        plan = example_plan()
        plan["tx_power_bound"]["tx_gain_db"] = 67
        with self.assertRaises(ValueError):
            validate(plan, tx_gain_db=67)

    def test_changed_serial_frequency_or_gain_requires_a_matching_plan(self):
        for settings in [
            {"device_serial": "OTHER-SERIAL"},
            {"frequency_hz": 916_000_000},
            {"tx_gain_db": -21},
        ]:
            with self.subTest(settings=settings), self.assertRaisesRegex(ValueError, "match"):
                validate(example_plan(), **settings)

    def test_unknown_or_automatic_device_selection_rejected(self):
        for serial in [None, "", "   ", "any", "*", "unknown", "later", True]:
            with self.subTest(serial=serial), self.assertRaises(ValueError):
                validate(example_plan(), device_serial=serial)

    def test_missing_fields_and_unknown_fields_are_rejected(self):
        for field in example_plan():
            plan = example_plan()
            del plan[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(plan)
        for field in ["tx_power_bound", "attenuators"]:
            plan = example_plan()
            item = plan[field] if field == "tx_power_bound" else plan[field][0]
            item["unknown_setting"] = True
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unsupported"):
                validate(plan)
        plan = example_plan()
        plan["over_the_air"] = True
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate(plan)

    def test_empty_or_wrong_schema_never_permits_live_tx(self):
        for plan in [None, {}, [], dict(example_plan(), schema="v1")]:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                validate(plan)

    def test_missing_connection_checks_and_active_antenna_rejected(self):
        for field, values in [
            ("operator_checked_connections", [False, 1, "true", None]),
            ("unused_ports_correctly_loaded", [False, 1, "true", None]),
            ("active_antenna_connected", [True, 0, "false", None]),
            ("connection", ["TX1_RX1", "OVER_THE_AIR", None]),
        ]:
            for value in values:
                plan = example_plan()
                plan[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate(plan)

    def test_empty_or_malformed_attenuator_chain_is_rejected(self):
        for stages in [[], None, {}, [None], [example_plan()["attenuators"][0]] * 33]:
            plan = example_plan()
            plan["attenuators"] = stages
            with self.subTest(stages=stages), self.assertRaises(ValueError):
                validate(plan)

    def test_frequency_coverage_includes_edges_and_rejects_out_of_band_and_reversed_bounds(self):
        for low, high, passes in [
            (915_000_000, 915_000_000, True),
            (916_000_000, 1_000_000_000, False),
            (800_000_000, 914_999_999, False),
            (1_000_000_000, 800_000_000, False),
        ]:
            plan = example_plan()
            plan["attenuators"][0].update(frequency_min_hz=low, frequency_max_hz=high)
            with self.subTest(low=low, high=high):
                if passes:
                    validate(plan)
                else:
                    with self.assertRaisesRegex(ValueError, "frequency coverage"):
                        validate(plan)

    def test_unknown_ratings_losses_and_bounds_are_rejected(self):
        for value in [None, "unknown", True, math.nan, math.inf, -math.inf]:
            for field in ["power_rating_w", "attenuation_db"]:
                plan = example_plan()
                plan["attenuators"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate(plan)
            for field in ["cable_loss_db", "rx_working_ceiling_dbm"]:
                plan = example_plan()
                plan[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate(plan)
            plan = example_plan()
            plan["tx_power_bound"]["upper_dbm"] = value
            with self.subTest(field="upper_dbm", value=value), self.assertRaises(ValueError):
                validate(plan)

    def test_component_labels_and_power_basis_must_be_known(self):
        for note in [None, "", "   ", "unknown", "later", "TBD", "unknown...", "?"]:
            plan = example_plan()
            plan["tx_power_bound"]["source_note"] = note
            with self.subTest(note=note), self.assertRaises(ValueError):
                validate(plan)
        plan = example_plan()
        plan["attenuators"][0]["label"] = ""
        with self.assertRaisesRegex(ValueError, "label"):
            validate(plan)
        plan = example_plan()
        plan["attenuators"] *= 2
        with self.assertRaisesRegex(ValueError, "distinct"):
            validate(plan)

    def test_zero_loss_is_allowed_but_zero_rating_and_negative_losses_are_not(self):
        plan = example_plan()
        plan["attenuators"].insert(0, dict(plan["attenuators"][0], label="Through stage", attenuation_db=0))
        validate(plan)
        for field, value in [("power_rating_w", 0), ("power_rating_w", -1), ("attenuation_db", -1)]:
            plan = example_plan()
            plan["attenuators"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(plan)
        plan = example_plan()
        plan["cable_loss_db"] = -1
        with self.assertRaises(ValueError):
            validate(plan)

    def test_boolean_fractional_or_out_of_policy_integer_settings_are_rejected(self):
        for settings in [
            {"frequency_hz": True}, {"frequency_hz": 915_000_000.0},
            {"frequency_hz": 69_999_999}, {"frequency_hz": 6_000_000_001},
            {"tx_gain_db": False}, {"tx_gain_db": -30.0},
            {"tx_gain_db": -24}, {"tx_gain_db": -30}, {"tx_gain_db": 67},
        ]:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                validate(example_plan(), **settings)
        for field in ["frequency_min_hz", "frequency_max_hz"]:
            plan = example_plan()
            plan["attenuators"][0][field] = True
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(plan)

    def test_returned_record_does_not_alias_operator_input(self):
        plan = example_plan()
        result = validate(plan)
        result["plan"]["attenuators"][0]["attenuation_db"] = 100
        self.assertEqual(plan["attenuators"][0]["attenuation_db"], 50)


if __name__ == "__main__":
    unittest.main()
