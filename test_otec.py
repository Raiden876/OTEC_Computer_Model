"""Scientific verification, limiting cases, and engineering constraint tests."""
from dataclasses import replace
from math import log10, sqrt
import unittest

from otec import Design, ModelError, darcy_factor, seawater, simulate


class EngineeringVerification(unittest.TestCase):
    def test_all_net_capacity_presets(self):
        for capacity in (1, 10, 25, 50):
            with self.subTest(capacity=capacity):
                r = simulate(Design.preset(capacity))
                self.assertAlmostEqual(r["net_mw"], capacity, places=8)
                self.assertGreater(r["gross_mw"], capacity)
                self.assertLess(r["first_law_relative_residual"], 1e-10)
                self.assertLess(r["electrical_relative_residual"], 1e-10)
                self.assertLess(r["cycle_efficiency"], r["cycle_carnot_efficiency"])
                self.assertLess(r["cycle_carnot_efficiency"], r["source_carnot_efficiency"])
                self.assertTrue(all(s >= -1e-5 for s in r["entropy_generation"].values()))
                # Independent aggregate control-volume energy account, including
                # generator and pump-motor losses but excluding seawater pumps.
                balance = (r["heat_input_mw"] - r["heat_rejected_mw"] - r["gross_mw"]
                           + r["wf_pump_mw"] - r["generator_loss_mw"] - r["wf_motor_loss_mw"])
                self.assertAlmostEqual(balance, 0, places=8)

    def test_gross_and_net_are_distinct(self):
        r = simulate(replace(Design(), capacity_basis="gross"))
        self.assertAlmostEqual(r["gross_mw"], 1)
        self.assertLess(r["net_mw"], 1)

    def test_ideal_adiabatic_components_generate_no_entropy(self):
        r = simulate(replace(Design(), turbine_eff=1, wf_pump_eff=1))
        self.assertAlmostEqual(r["entropy_generation"]["pump_w_k"], 0, places=6)
        self.assertAlmostEqual(r["entropy_generation"]["turbine_w_k"], 0, places=6)

    def test_source_carnot_kelvin_conversion(self):
        r = simulate()
        self.assertAlmostEqual(r["source_carnot_efficiency"], 23 / 301.15, places=12)

    def test_seawater_heat_account_agrees_with_preliminary_cp(self):
        r = simulate()
        # Independent engineering approximation cp≈4000 J/(kg K).
        for circuit, duty in (("warm", r["heat_input_mw"]), ("cold", r["heat_rejected_mw"])):
            approximate = r[circuit + "_hydraulics"]["mass_flow_kg_s"] * 4000 * 3 / 1e6
            self.assertLess(abs(approximate / duty - 1), .015)

    def test_heat_exchanger_mesh_convergence(self):
        coarse, fine = simulate(segments=24), simulate(segments=96)
        for name in ("evaporator", "condenser"):
            self.assertLess(abs(coarse[name]["area_m2"] / fine[name]["area_m2"] - 1), 1e-4)

    def test_smaller_pipes_need_more_gross_power_for_same_net(self):
        base = simulate()
        narrow = simulate(replace(Design(), warm_diameter_m=2.1, cold_diameter_m=2.1))
        self.assertAlmostEqual(narrow["net_mw"], base["net_mw"])
        self.assertGreater(narrow["gross_mw"], base["gross_mw"])

    def test_fouling_increases_required_area_without_changing_duty(self):
        clean = simulate(replace(Design(), fouling_m2k_w=0))
        fouled = simulate(replace(Design(), fouling_m2k_w=.0001))
        self.assertEqual(clean["heat_input_mw"], fouled["heat_input_mw"])
        self.assertGreater(fouled["evaporator"]["area_m2"], clean["evaporator"]["area_m2"])

    def test_pipe_length_is_friction_not_lifting_head(self):
        r = simulate()
        self.assertLess(r["cold_hydraulics"]["total_head_m"], 10)
        self.assertEqual(r["inputs"]["cold_length_m"], 1200)

    def test_negative_gross_design_net_output_is_not_clamped(self):
        r = simulate(replace(Design(), capacity_basis="gross", cold_diameter_m=.6))
        self.assertLess(r["net_mw"], 0)

    def test_undersized_pipes_cannot_meet_net_capacity(self):
        with self.assertRaisesRegex(ModelError, "Cannot deliver"):
            simulate(replace(Design(), cold_diameter_m=.6))

    def test_target_close_to_hydraulic_peak_is_not_missed(self):
        r = simulate(replace(Design(), cold_diameter_m=.6, capacity_mw=.158))
        self.assertAlmostEqual(r["net_mw"], .158, places=10)

    def test_non_finite_output_from_extreme_heat_transfer_input_is_rejected(self):
        with self.assertRaisesRegex(ModelError, "Non-finite"):
            simulate(replace(Design(), evaporator_u=1e-308))

    def test_laminar_darcy_factor(self):
        self.assertAlmostEqual(darcy_factor(1000, .001), .064)

    def test_turbulent_colebrook_residual(self):
        f = darcy_factor(100000, .0001)
        self.assertAlmostEqual(1 / sqrt(f) + 2 * log10(.0001 / 3.7 + 2.51 / (100000 * sqrt(f))), 0, places=8)
        self.assertAlmostEqual(f, .018514, places=6)

    def test_density_has_reasonable_salinity_dependence(self):
        fresh = seawater(0, 28, 125, 8)
        sea = seawater(35, 28, 125, 8)
        self.assertGreater(sea["density_kg_m3"], fresh["density_kg_m3"] + 20)
        self.assertTrue(1020 < sea["density_kg_m3"] < 1025)

    def test_crossed_heat_exchanger_and_insufficient_pinch_rejected(self):
        for updates in ({"evaporator_c": 27}, {"condenser_c": 6}, {"minimum_pinch_k": 4}):
            with self.subTest(updates=updates), self.assertRaisesRegex(ModelError, "pinch"):
                simulate(replace(Design(), **updates))

    def test_wetness_constraint_is_enforced(self):
        with self.assertRaisesRegex(ModelError, "quality"):
            simulate(replace(Design(), minimum_quality=1))

    def test_invalid_inputs_rejected(self):
        for updates in ({"warm_c": float("nan")}, {"capacity_mw": -1}, {"generator_eff": 1.01},
                        {"warm_pipes": 1.5}, {"cold_viscosity_mpas": 0}, {"cold_sp": 50},
                        {"cold_static_head_m": -1}, {"capacity_basis": "invalid"}):
            with self.subTest(updates=updates), self.assertRaises(ModelError):
                simulate(replace(Design(), **updates))

    def test_freezing_water_rejected(self):
        with self.assertRaisesRegex(ModelError, "freezing"):
            seawater(0, -.1, 125, 8)

    def test_unknown_input_rejected(self):
        with self.assertRaisesRegex(ModelError, "Unknown"):
            Design.from_dict({"capcity_mw": 1})


if __name__ == "__main__":
    unittest.main()
