"""Behavioral tests of the optional Streamlit interface, without a browser."""
import importlib.util
from pathlib import Path
import unittest


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Optional Streamlit package is not installed")
class StreamlitBehavior(unittest.TestCase):
    def setUp(self):
        from streamlit.testing.v1 import AppTest
        self.app = AppTest.from_file(str(Path(__file__).with_name("streamlit_app.py")), default_timeout=20).run()
        self.assertFalse(self.app.exception)

    def test_initial_design_and_explicit_calculation(self):
        app = self.app
        self.assertAlmostEqual(app.session_state["result"]["net_mw"], 1)
        app.number_input(key="d_evaporator_c").set_value(21.0)
        app.button(key="calculate_design").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["applied"]["evaporator_c"], 21)
        self.assertAlmostEqual(app.session_state["result"]["net_mw"], 1)


    def test_dynamic_capacity_value_and_unit(self):
        app = self.app
        app.selectbox(key="d_capacity_unit").set_value("kW").run()
        self.assertEqual(app.number_input(key="d_capacity_value").value, 1000)
        app.number_input(key="d_capacity_value").set_value(500).run()
        app.button(key="calculate_design").click().run()
        self.assertFalse(app.exception)
        self.assertAlmostEqual(app.session_state["result"]["net_mw"], .5, places=8)
        self.assertEqual(app.session_state["applied"]["warm_pipes"], 1)

        app.selectbox(key="d_capacity_unit").set_value("MW").run()
        self.assertEqual(app.number_input(key="d_capacity_value").value, .5)
        app.number_input(key="d_capacity_value").set_value(10).run()
        app.button(key="calculate_design").click().run()
        self.assertFalse(app.exception)
        self.assertAlmostEqual(app.session_state["result"]["net_mw"], 10, places=8)
        self.assertEqual(app.session_state["applied"]["warm_pipes"], 2)

    def test_apply_refreshes_all_performance_views(self):
        app = self.app
        def chart_payloads():
            return [(e.proto.spec, e.proto.data.SerializeToString(),
                     tuple(data.SerializeToString() for data in e.proto.datasets))
                    for e in app.get("vega_lite_chart")][1:]

        before_charts = chart_payloads()
        before_tables = [e.proto.body for e in app.get("html") if "<table" in e.proto.body]
        app.number_input(key="d_capacity_value").set_value(2.0).run()
        app.number_input(key="d_evaporator_c").set_value(21.0).run()
        self.assertEqual(app.session_state["applied"]["capacity_mw"], 1.0)
        self.assertEqual(before_charts, chart_payloads())
        self.assertEqual(before_tables, [e.proto.body for e in app.get("html") if "<table" in e.proto.body])

        app.button(key="calculate_design").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["applied"]["capacity_mw"], 2.0)
        self.assertEqual(app.session_state["applied"]["evaporator_c"], 21.0)
        after_charts = chart_payloads()
        after_tables = [e.proto.body for e in app.get("html") if "<table" in e.proto.body]
        self.assertEqual(len(after_charts), 9)
        self.assertEqual(len(after_tables), 5)
        ammonia_table = next(t for t in after_tables if "Ammonia circulation" in t)
        self.assertIn(f'{app.session_state["result"]["wf_mass_kg_s"]:,.1f}', ammonia_table)
        self.assertTrue(all(a != b for a, b in zip(before_charts, after_charts)))
        self.assertTrue(all(a != b for a, b in zip(before_tables, after_tables)))

    def test_apply_keeps_explicit_cycle_edit_when_profile_changes(self):
        app = self.app
        app.session_state["d_ocean_profile_editor"] = {
            "edited_rows": {0: {"temperature_c": 27.0}},
            "added_rows": [], "deleted_rows": [],
        }
        app.number_input(key="d_evaporator_c").set_value(21.5).run()
        app.button(key="calculate_design").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["applied"]["warm_c"], 27.0)
        self.assertEqual(app.session_state["applied"]["evaporator_c"], 21.5)
        self.assertAlmostEqual(app.session_state["result"]["states"][2]["temperature_c"], 21.5)

    def test_depth_selection_drives_source_temperature(self):
        app = self.app
        app.slider(key="ocean_cold_depth").set_value(500.0).run()
        # Preview alone must not change the calculated plant.
        self.assertEqual(app.session_state["applied"]["cold_c"], 5)
        app.button(key="apply_depth").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["applied"]["cold_c"], 9)
        self.assertEqual(app.session_state["applied"]["cold_intake_depth_m"], 500)
        self.assertEqual(app.session_state["applied"]["condenser_c"], 14)
        self.assertAlmostEqual(app.session_state["result"]["net_mw"], 1)
        self.assertEqual(app.session_state["result"]["ocean_resource"]["cold_depth_m"], 500)

    def test_invalid_design_preserves_previous_result_and_disables_exports(self):
        app = self.app
        app.number_input(key="d_cold_length_m").set_value(900.0)
        app.button(key="calculate_design").click().run()
        self.assertFalse(app.exception)
        self.assertIn("shorter than intake depth", app.session_state["error"])
        self.assertAlmostEqual(app.session_state["result"]["net_mw"], 1)
        self.assertTrue(app.button(key="compare_capacities").disabled)
        result_downloads = [e for e in app.get("download_button") if "result" in e.proto.label or "Engineering" in e.proto.label]
        self.assertTrue(result_downloads)
        self.assertTrue(all(e.proto.disabled for e in result_downloads))

    def test_four_capacity_comparison(self):
        app = self.app
        app.button(key="compare_capacities").click().run()
        self.assertFalse(app.exception)
        cases = app.session_state["comparison"]
        self.assertEqual(len(cases), 4)
        for case in cases:
            self.assertNotIn("error", case)
            self.assertAlmostEqual(case["result"]["net_mw"], case["capacity"], places=8)

    def test_invalid_resource_prevents_application(self):
        app = self.app
        app.slider(key="ocean_warm_depth").set_value(1100.0).run()
        self.assertFalse(app.exception)
        self.assertTrue(app.button(key="apply_depth").disabled)
        self.assertEqual(app.session_state["applied"]["cold_c"], 5)

    def test_illustrative_profile_can_be_restored_without_changing_plant(self):
        app = self.app
        self.assertTrue(any("#### Illustrative tropical profile" in m.value for m in app.markdown))
        app.slider(key="ocean_cold_depth").set_value(500.0).run()
        app.button(key="apply_depth").click().run()
        app.text_area(key="profile_text").set_value("").run()
        self.assertTrue(app.button(key="apply_depth").disabled)
        app.button(key="restore_profile").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.button(key="apply_depth").disabled)
        self.assertIn("1000,5,35", app.session_state["profile_text"])
        self.assertEqual(app.slider(key="ocean_cold_depth").value, 1000)
        self.assertEqual(app.session_state["applied"]["cold_c"], 9)
        self.assertTrue(any("#### Illustrative tropical profile" in m.value for m in app.markdown))

    def test_profile_is_sent_as_real_image_media_and_updates_with_depth(self):
        # AppTest cannot render browser charts; checking actual image media
        # prevents a valid chart specification from masking another blank view.
        app = self.app
        images = app.get("image")
        self.assertEqual(len(images), 1)
        before = images[0].proto.imgs[0].url
        self.assertTrue(before.endswith(".png"), before)
        app.slider(key="ocean_cold_depth").set_value(500.0).run()
        self.assertFalse(app.exception)
        after = app.get("image")[0].proto.imgs[0].url
        self.assertTrue(after.endswith(".png"), after)
        self.assertNotEqual(before, after)

    def test_reset_recovers_from_invalid_design(self):
        app = self.app
        app.number_input(key="d_evaporator_c").set_value(27.0)
        app.button(key="calculate_design").click().run()
        self.assertTrue(app.session_state["error"])
        app.button(key="reset_design").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["error"], "")
        self.assertEqual(app.session_state["applied"]["evaporator_c"], 22)


if __name__ == "__main__":
    unittest.main()
