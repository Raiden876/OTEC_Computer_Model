#!/usr/bin/env python3
"""Run with .venv/bin/python app.py; opens a local, dependency-free browser UI."""
import argparse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from math import isfinite
from pathlib import Path
import secrets
import sys
from threading import Timer
import webbrowser

try:
    from otec import Design, ModelError, simulate
    from ocean import resource_scenario
except ImportError as exc:
    raise SystemExit("Missing scientific libraries. Run:\n  .venv/bin/python -m pip install -r requirements.txt\n"
                     f"Details: {exc}") from exc


# name, user-facing label, step, explanation
GROUPS = [
    ("Ocean at the heat exchangers", [
        ("warm_c", "Warm inlet · °C", .1, "In-situ seawater temperature arriving at the evaporator."),
        ("cold_c", "Cold inlet · °C", .1, "In-situ seawater temperature arriving at the condenser, after ascent."),
        ("warm_sp", "Warm practical salinity · SP", .1, "Practical salinity, not Absolute Salinity in g/kg."),
        ("cold_sp", "Cold practical salinity · SP", .1, "Converted to Absolute Salinity with TEOS-10."),
        ("longitude", "Longitude · °E", .1, "Used only for the TEOS-10 salinity conversion; no ocean data is fetched."),
        ("latitude", "Latitude · °N", .1, "Illustrative Mindanao coordinates, not a validated site.")]),
    ("Cycle & seawater temperature changes", [
        ("evaporator_c", "Evaporation · °C", .1, "Saturated ammonia vapor enters the turbine."),
        ("condenser_c", "Condensation · °C", .1, "Saturated ammonia liquid enters the working-fluid pump."),
        ("warm_drop_k", "Warm-water cooling · K", .1, "Determines required warm flow from the exact seawater enthalpy change."),
        ("cold_rise_k", "Cold-water warming · K", .1, "Determines required cold flow from condenser duty."),
        ("minimum_pinch_k", "Minimum HX approach · K", .1, "All sampled points, including phase boundaries, must pass."),
        ("minimum_quality", "Minimum turbine exit quality", .01, "User-selected wetness criterion, not a standard or vendor limit.")]),
    ("Efficiencies & auxiliaries", [
        ("turbine_eff", "Turbine isentropic efficiency", .01, "Actual enthalpy drop / isentropic enthalpy drop."),
        ("generator_eff", "Generator efficiency", .01, "Electrical output / turbine shaft power."),
        ("wf_pump_eff", "Ammonia pump isentropic efficiency", .01, "Isentropic enthalpy rise / actual enthalpy rise."),
        ("wf_motor_eff", "Ammonia pump motor efficiency", .01, "Shaft input / electrical input."),
        ("seawater_pump_eff", "Seawater hydraulic efficiency", .01, "Hydraulic power / pump shaft input."),
        ("seawater_motor_eff", "Seawater motor efficiency", .01, "Pump shaft input / electrical input."),
        ("auxiliary_fraction", "Auxiliaries / gross output", .005, "Other loads as a fraction of gross generator output."),
        ("auxiliary_kw", "Additional fixed auxiliaries · kW", 1, "Added once; working-fluid and seawater pumps are accounted separately.")]),
    ("Warm-water circuit", [
        ("warm_intake_depth_m", "Intake depth · m", 1, "Geometric check only in manual mode. Depth-profile sandbox also determines source temperature."),
        ("warm_length_m", "Total pipe path per branch · m", 10, "Intake plus discharge equivalent length, not intake depth."),
        ("warm_diameter_m", "Internal diameter per pipe · m", .01, "Equal-flow parallel branches; illustrative geometry."),
        ("warm_pipes", "Parallel pipes", 1, "Identical branches share total flow equally."),
        ("warm_minor_k", "Sum of minor-loss K", .1, "Based on velocity inside each branch."),
        ("warm_hx_dp_kpa", "Seawater HX pressure drop · kPa", 1, "Specified independently; no exchanger geometry correlation."),
        ("warm_static_head_m", "Additional unrecovered head · m", .1, "Net unbalanced elevation/density/discharge head; include site-specific losses."),
        ("warm_viscosity_mpas", "Dynamic viscosity · mPa·s", .01, "Explicit user estimate; not calculated by TEOS-10.")]),
    ("Cold-water circuit", [
        ("cold_intake_depth_m", "Intake depth · m", 10, "Geometric lower bound for pipe length; never automatically used as pumping head."),
        ("cold_length_m", "Total pipe path per branch · m", 10, "Friction length; does not become hydrostatic lifting head."),
        ("cold_diameter_m", "Internal diameter per pipe · m", .01, "Equal-flow parallel branches; not a structural design."),
        ("cold_pipes", "Parallel pipes", 1, "Identical branches share total flow equally."),
        ("cold_minor_k", "Sum of minor-loss K", .1, "Includes assumed intake, fittings and discharge losses."),
        ("cold_hx_dp_kpa", "Seawater HX pressure drop · kPa", 1, "Specified independently of required heat-transfer area."),
        ("cold_static_head_m", "Additional unrecovered head · m", .1, "Do not enter intake depth automatically. Derive from the hydraulic layout."),
        ("cold_viscosity_mpas", "Dynamic viscosity · mPa·s", .01, "Explicit user estimate at mean circuit conditions.")]),
    ("Heat-exchanger sizing & pipe surface", [
        ("evaporator_u", "Clean evaporator U · W/(m²·K)", 50, "Assumed overall coefficient, not predicted from channel geometry."),
        ("condenser_u", "Clean condenser U · W/(m²·K)", 50, "Area is integrated over counterflow enthalpy segments."),
        ("fouling_m2k_w", "Added fouling resistance · m²·K/W", .00001, "Added to 1/U on the same area basis."),
        ("roughness_mm", "Absolute pipe roughness · mm", .01, "Used by the Darcy friction factor; same for both circuits.")]),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check", action="store_true", help="Solve a design, print JSON, and exit without a GUI")
    parser.add_argument("--capacity", type=float, default=1, help="Target electrical capacity (default: 1)")
    parser.add_argument("--unit", choices=("kW", "MW"), default="MW",
                        help="Unit for --capacity (default: MW)")
    parser.add_argument("--basis", choices=("net", "gross"), default="net")
    args = parser.parse_args()
    capacity_mw = args.capacity / 1000 if args.unit == "kW" else args.capacity
    if not isfinite(capacity_mw) or not .01 <= capacity_mw <= 100:
        parser.error("--capacity must be between 10 kW and 100 MW")
    default = asdict(Design.preset(capacity_mw))
    default["capacity_basis"] = args.basis
    if args.check:
        try:
            print(json.dumps(simulate(Design.from_dict(default)), indent=2, allow_nan=False))
        except ModelError as exc:
            parser.exit(1, str(exc) + "\n")
        return
    token = secrets.token_urlsafe(32)
    html = Path(__file__).with_name("ui.html").read_text(encoding="utf-8")
    html = html.replace("__BOOTSTRAP__", json.dumps({"defaults": default, "groups": GROUPS, "token": token}))

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, data, content_type="application/json; charset=utf-8"):
            body = data.encode("utf-8") if isinstance(data, str) else json.dumps(data, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def do_GET(self):
            if not self.valid_host():
                self.reply(403, {"error": "Use the localhost address printed in your terminal."})
            elif self.path in ("/", "/index.html"):
                self.reply(200, html, "text/html; charset=utf-8")
            elif self.path == "/favicon.ico":
                self.reply(204, "")
            else:
                self.reply(404, {"error": "Not found"})

        def do_POST(self):
            if not self.valid_host() or not secrets.compare_digest(self.headers.get("X-OTEC-Token", ""), token):
                self.reply(403, {"error": "Refresh the app to establish a local session."})
                return
            if self.path not in ("/api/simulate", "/api/resource"):
                self.reply(404, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ModelError("Request must be between 1 and 32768 bytes.")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ModelError("Expected an input object.")
                self.reply(200, resource_scenario(data) if self.path == "/api/resource" else simulate(Design.from_dict(data)))
            except (ValueError, TypeError, ModelError) as exc:
                self.reply(422, {"error": str(exc)})
            except Exception as exc:
                print(f"Unexpected solver error: {exc}", file=sys.stderr)
                self.reply(500, {"error": "Unexpected solver error; see the terminal."})

        def log_message(self, *_):
            pass

    try:
        server = HTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        parser.exit(1, f"Cannot start the local GUI: {exc}. Try --port 8502.\n")
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"OTEC simulator: {url}\nLocal only. Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
