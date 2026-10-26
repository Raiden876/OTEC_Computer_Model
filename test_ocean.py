"""Resource interpolation, seawater pressure units, and ascent verification."""
from dataclasses import replace
import unittest

import gsw

from ocean import resource_scenario
from otec import Design, ModelError, seawater, simulate


def scenario(**updates):
    data = {"longitude": 125, "latitude": 8, "temperature_type": "in_situ",
            "warm_depth_m": 10, "cold_depth_m": 1000,
            "profile": [{"depth_m": z, "temperature_c": t, "salinity_sp": 35}
                        for z, t in ((10, 28), (100, 24), (200, 18), (500, 9), (1000, 5), (1500, 4))]}
    return resource_scenario({**data, **updates})


class OceanResourceVerification(unittest.TestCase):
    def test_selected_depth_controls_temperature(self):
        shallow, deep = scenario(cold_depth_m=500), scenario(cold_depth_m=1000)
        self.assertEqual(shallow["cold"]["ocean_in_situ_c"], 9)
        self.assertEqual(deep["cold"]["ocean_in_situ_c"], 5)
        self.assertGreater(deep["plant_delta_k"], shallow["plant_delta_k"])

    def test_interpolation_between_supplied_levels(self):
        r = scenario(cold_depth_m=750)
        self.assertAlmostEqual(r["cold"]["profile_temperature_c"], 7)
        self.assertTrue(740 < r["cold"]["pressure_dbar"] < 780)

    def test_adiabatic_ascent_preserves_entropy(self):
        cold = scenario()["cold"]
        sa = cold["absolute_salinity_g_kg"]
        s_at_depth = gsw.entropy_from_t(sa, cold["ocean_in_situ_c"], cold["pressure_dbar"])
        s_at_plant = gsw.entropy_from_t(sa, cold["plant_inlet_c"], 0)
        self.assertAlmostEqual(s_at_plant, s_at_depth, places=7)
        self.assertLess(cold["plant_inlet_c"], cold["ocean_in_situ_c"])

    def test_potential_temperature_is_not_mistaken_for_in_situ(self):
        r = scenario(temperature_type="potential_0dbar")
        self.assertEqual(r["cold"]["plant_inlet_c"], 5)
        self.assertGreater(r["cold"]["ocean_in_situ_c"], 5)

    def test_absolute_salinity_is_carried_to_plant(self):
        r = scenario()["cold"]
        plant = seawater(r["plant_equivalent_sp"], r["plant_inlet_c"], 125, 8)
        self.assertAlmostEqual(plant["sa_g_kg"], r["absolute_salinity_g_kg"], places=10)

    def test_profile_can_drive_engineering_solver(self):
        ocean = scenario()
        r = simulate(replace(Design(), warm_c=ocean["warm"]["plant_inlet_c"],
                             cold_c=ocean["cold"]["plant_inlet_c"],
                             warm_sp=ocean["warm"]["plant_equivalent_sp"],
                             cold_sp=ocean["cold"]["plant_equivalent_sp"]))
        self.assertAlmostEqual(r["net_mw"], 1, places=8)

    def test_extrapolation_and_reversed_intakes_rejected(self):
        for changes in ({"cold_depth_m": 1600}, {"warm_depth_m": 0},
                        {"warm_depth_m": 1000, "cold_depth_m": 500}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ModelError, "inside the profile"):
                scenario(**changes)

    def test_duplicate_and_invalid_profile_values_rejected(self):
        for rows in ([{"depth_m": 10, "temperature_c": 28, "salinity_sp": 35}] * 2,
                     [{"depth_m": 10, "temperature_c": float("nan"), "salinity_sp": 35},
                      {"depth_m": 1000, "temperature_c": 5, "salinity_sp": 35}]):
            with self.subTest(rows=rows), self.assertRaises(ModelError):
                scenario(profile=rows)

    def test_pipe_reach_is_an_independent_constraint(self):
        with self.assertRaisesRegex(ModelError, "shorter than intake depth"):
            simulate(replace(Design(), cold_intake_depth_m=1500))


if __name__ == "__main__":
    unittest.main()
