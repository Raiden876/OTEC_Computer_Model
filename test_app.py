"""Local HTTP integration checks. Requires permission to bind a loopback socket."""
from dataclasses import asdict
import http.client
import json
from pathlib import Path
import re
import select
import subprocess
import sys
import unittest

from otec import Design


class LocalGUIIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = subprocess.Popen([sys.executable, "app.py", "--no-browser", "--port", "0"],
                                      cwd=Path(__file__).parent, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
        ready, _, _ = select.select([cls.server.stdout], [], [], 25)
        line = cls.server.stdout.readline() if ready else ""
        match = re.search(r"http://127.0.0.1:(\d+)", line)
        if not match:
            cls.server.terminate()
            out, err = cls.server.communicate(timeout=5)
            raise RuntimeError("GUI failed to start: " + line + out + err)
        cls.port = int(match[1])
        status, html = cls.request("GET", "/")
        if status != 200:
            raise RuntimeError("Cannot fetch local GUI")
        cls.bootstrap = json.loads(re.search(r"const boot=(.*?);\n", html).group(1))

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.communicate(timeout=5)

    @classmethod
    def request(cls, method, path, data=None, headers=None):
        con = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=10)
        try:
            con.request(method, path, data, headers or {})
            response = con.getresponse()
            return response.status, response.read().decode()
        finally:
            con.close()

    def post(self, data):
        return self.request("POST", "/api/simulate", json.dumps(data),
                            {"X-OTEC-Token": self.bootstrap["token"], "Content-Type": "application/json"})

    def test_html_contains_gui_and_no_external_dependencies(self):
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Compare all four capacities", html)
        self.assertNotIn("__BOOTSTRAP__", html)
        self.assertNotRegex(html, r'<script[^>]+src=|<link[^>]+href=')

    def test_gui_api_solves_all_net_presets(self):
        for capacity in (1, 10, 25, 50):
            with self.subTest(capacity=capacity):
                status, text = self.post(asdict(Design.preset(capacity)))
                self.assertEqual(status, 200, text)
                self.assertAlmostEqual(json.loads(text)["net_mw"], capacity, places=8)

    def test_infeasible_design_returns_actionable_error(self):
        status, text = self.post({"evaporator_c": 27})
        self.assertEqual(status, 422)
        self.assertIn("pinch", json.loads(text)["error"])

    def test_untrusted_requests_are_rejected(self):
        status, _ = self.request("POST", "/api/simulate", "{}")
        self.assertEqual(status, 403)
        status, _ = self.request("GET", "/", headers={"Host": "external.invalid"})
        self.assertEqual(status, 403)

    def test_malformed_json_returns_error(self):
        status, _ = self.request("POST", "/api/simulate", "{", {"X-OTEC-Token": self.bootstrap["token"]})
        self.assertEqual(status, 422)

    def test_depth_resource_api(self):
        data = {"longitude": 125, "latitude": 8, "temperature_type": "in_situ",
                "warm_depth_m": 10, "cold_depth_m": 1000,
                "profile": [{"depth_m": 10, "temperature_c": 28, "salinity_sp": 35},
                            {"depth_m": 1000, "temperature_c": 5, "salinity_sp": 35}]}
        status, text = self.request("POST", "/api/resource", json.dumps(data),
                                    {"X-OTEC-Token": self.bootstrap["token"]})
        self.assertEqual(status, 200, text)
        self.assertLess(json.loads(text)["cold"]["plant_inlet_c"], 5)

    def test_no_arbitrary_file_serving(self):
        status, _ = self.request("GET", "/otec.py")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
