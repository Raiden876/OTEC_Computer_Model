"""Steady-state, design-point R717 OTEC sizing model. All internal units are SI.

This is equation verification software, not an experimentally validated plant model.
See README.md for the boundary conditions, equations, and omissions.
"""
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from math import ceil, isfinite, log, log10, pi, sqrt
import platform

import CoolProp
from CoolProp.CoolProp import PropsSI
import gsw

FLUID = "Ammonia"
G = 9.80665
VERSION = "0.1.0"


class ModelError(ValueError):
    """Invalid inputs or an infeasible design point."""


@dataclass(frozen=True)
class Design:
    capacity_mw: float = 1.0
    capacity_basis: str = "net"
    warm_c: float = 28.0
    cold_c: float = 5.0
    warm_sp: float = 35.0
    cold_sp: float = 35.0
    longitude: float = 125.0
    latitude: float = 8.0
    evaporator_c: float = 22.0
    condenser_c: float = 10.0
    warm_drop_k: float = 3.0
    cold_rise_k: float = 3.0
    minimum_pinch_k: float = 1.0
    turbine_eff: float = 0.85
    generator_eff: float = 0.96
    wf_pump_eff: float = 0.75
    wf_motor_eff: float = 0.95
    seawater_pump_eff: float = 0.80
    seawater_motor_eff: float = 0.95
    auxiliary_fraction: float = 0.02
    auxiliary_kw: float = 0.0
    warm_length_m: float = 200.0
    cold_length_m: float = 1200.0
    warm_intake_depth_m: float = 10.0
    cold_intake_depth_m: float = 1000.0
    warm_diameter_m: float = 2.4
    cold_diameter_m: float = 2.4
    warm_pipes: float = 1.0
    cold_pipes: float = 1.0
    roughness_mm: float = 0.05
    warm_minor_k: float = 3.0
    cold_minor_k: float = 3.0
    warm_hx_dp_kpa: float = 20.0
    cold_hx_dp_kpa: float = 20.0
    warm_static_head_m: float = 0.0
    cold_static_head_m: float = 0.0
    warm_viscosity_mpas: float = 0.95
    cold_viscosity_mpas: float = 1.55
    evaporator_u: float = 3000.0
    condenser_u: float = 3000.0
    fouling_m2k_w: float = 0.00003
    minimum_quality: float = 0.90

    @classmethod
    def preset(cls, capacity):
        n = ceil(capacity / 5)
        return cls(capacity_mw=capacity, warm_pipes=n, cold_pipes=n,
                   warm_diameter_m=2.4 * sqrt(capacity / n),
                   cold_diameter_m=2.4 * sqrt(capacity / n))

    @classmethod
    def from_dict(cls, data):
        names = {f.name for f in fields(cls)}
        if set(data) - names:
            raise ModelError("Unknown input fields: " + ", ".join(sorted(set(data) - names)))
        try:
            return cls(**{k: (str(v) if k == "capacity_basis" else float(v))
                          for k, v in data.items()})
        except (ValueError, TypeError) as exc:
            raise ModelError("Every numerical input must be a finite number.") from exc

    def validate(self):
        for key, value in asdict(self).items():
            if key != "capacity_basis" and not isfinite(value):
                raise ModelError(f"{key} must be finite.")
        if self.capacity_basis not in ("net", "gross"):
            raise ModelError("Capacity basis must be net or gross.")
        if not 0.01 <= self.capacity_mw <= 100:
            raise ModelError("Capacity must be between 0.01 and 100 MW.")
        if not -2 <= self.cold_c < self.warm_c <= 40:
            raise ModelError("Require -2 ≤ cold inlet < warm inlet ≤ 40 °C.")
        if not self.cold_c < self.condenser_c < self.evaporator_c < self.warm_c:
            raise ModelError("Require cold inlet < condenser < evaporator < warm inlet.")
        if not (-180 <= self.longitude <= 180 and -90 <= self.latitude <= 90):
            raise ModelError("Invalid longitude or latitude.")
        for name in ("warm_sp", "cold_sp"):
            if not 0 <= getattr(self, name) <= 42:
                raise ModelError("Practical salinity must be between 0 and 42.")
        for name in ("turbine_eff", "generator_eff", "wf_pump_eff", "wf_motor_eff",
                     "seawater_pump_eff", "seawater_motor_eff"):
            if not 0.1 <= getattr(self, name) <= 1:
                raise ModelError(f"{name} must be between 0.1 and 1.")
        positive = ("warm_drop_k", "cold_rise_k", "minimum_pinch_k", "warm_diameter_m",
                    "cold_diameter_m", "warm_viscosity_mpas", "cold_viscosity_mpas",
                    "evaporator_u", "condenser_u")
        for name in positive:
            if getattr(self, name) <= 0:
                raise ModelError(f"{name} must be greater than zero.")
        for name in ("warm_pipes", "cold_pipes"):
            v = getattr(self, name)
            if not 1 <= v <= 100 or int(v) != v:
                raise ModelError("Parallel pipe counts must be integers between 1 and 100.")
        for name in ("warm_length_m", "cold_length_m", "warm_intake_depth_m", "cold_intake_depth_m", "roughness_mm", "warm_minor_k",
                     "cold_minor_k", "warm_hx_dp_kpa", "cold_hx_dp_kpa",
                     "warm_static_head_m", "cold_static_head_m", "fouling_m2k_w", "auxiliary_kw"):
            if getattr(self, name) < 0:
                raise ModelError(f"{name} cannot be negative.")
        for side in ("warm", "cold"):
            if getattr(self, side + "_length_m") < getattr(self, side + "_intake_depth_m"):
                raise ModelError(f"{side.capitalize()} pipe path cannot be shorter than intake depth for a plant at sea level.")
        if not 0 <= self.auxiliary_fraction < 1 or not 0 <= self.minimum_quality <= 1:
            raise ModelError("Invalid auxiliary fraction or minimum turbine quality.")
        if self.warm_c - self.warm_drop_k <= self.cold_c:
            raise ModelError("Warm-water cooling is too large for the source temperatures.")
        if self.cold_c + self.cold_rise_k >= self.warm_c:
            raise ModelError("Cold-water warming is too large for the source temperatures.")
        if self.roughness_mm / 1000 >= min(self.warm_diameter_m, self.cold_diameter_m) * 0.05:
            raise ModelError("Pipe relative roughness must be less than 0.05.")


def darcy_factor(reynolds, relative_roughness):
    """64/Re laminar; Colebrook turbulent; linear transitional interpolation."""
    if reynolds <= 0:
        raise ModelError("Reynolds number must be positive.")
    def turbulent(re):
        f = 0.02
        for _ in range(30):
            new = (-2 * log10(relative_roughness / 3.7 + 2.51 / (re * sqrt(f)))) ** -2
            if abs(new - f) < 1e-12:
                return new
            f = new
        return f
    if reynolds <= 2300:
        return 64 / reynolds
    if reynolds < 4000:
        weight = (reynolds - 2300) / 1700
        return (1 - weight) * 64 / 2300 + weight * turbulent(4000)
    return turbulent(reynolds)


def seawater(sp, temperature, longitude, latitude):
    """Properties at exchanger pressure p=0 dbar; input is in-situ HX inlet T."""
    sa = float(gsw.SA_from_SP(sp, 0, longitude, latitude))
    if temperature <= float(gsw.t_freezing(sa, 0, 0)):
        raise ModelError("Seawater temperature is at or below its freezing point.")
    ct = float(gsw.CT_from_t(sa, temperature, 0))
    return {"sa_g_kg": sa, "density_kg_m3": float(gsw.rho(sa, ct, 0)),
            "h_j_kg": float(gsw.enthalpy_t_exact(sa, temperature, 0)),
            "s_j_kgk": float(gsw.entropy_from_t(sa, temperature, 0))}


def sw_temperature(sa, enthalpy):
    """Invert the exact Gibbs enthalpy with a bounded bisection (no SciPy)."""
    lo, hi = -3.0, 45.0
    for _ in range(36):
        mid = (lo + hi) / 2
        if gsw.enthalpy_t_exact(sa, mid, 0) < enthalpy:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def hydraulics(d, side, mass_flow, density):
    diameter = getattr(d, side + "_diameter_m")
    count = getattr(d, side + "_pipes")
    volume = mass_flow / density
    velocity = volume / (count * pi * diameter ** 2 / 4)
    re = density * velocity * diameter / (getattr(d, side + "_viscosity_mpas") / 1000)
    f = darcy_factor(re, d.roughness_mm / 1000 / diameter)
    friction = f * getattr(d, side + "_length_m") / diameter * velocity ** 2 / (2 * G)
    minor = getattr(d, side + "_minor_k") * velocity ** 2 / (2 * G)
    hx = getattr(d, side + "_hx_dp_kpa") * 1000 / (density * G)
    head = friction + minor + hx + getattr(d, side + "_static_head_m")
    power = density * G * volume * head / (d.seawater_pump_eff * d.seawater_motor_eff)
    return {"mass_flow_kg_s": mass_flow, "volume_m3_s": volume, "velocity_m_s": velocity,
            "reynolds": re, "darcy_factor": f, "friction_head_m": friction,
            "minor_head_m": minor, "hx_head_m": hx, "total_head_m": head,
            "electrical_w": power, "density_kg_m3": density}


def state(number, label, pressure, enthalpy):
    quality = PropsSI("Q", "P", pressure, "H", enthalpy, FLUID)
    return {"state": number, "location": label, "pressure_bar": pressure / 1e5,
            "temperature_c": PropsSI("T", "P", pressure, "H", enthalpy, FLUID) - 273.15,
            "enthalpy_kj_kg": enthalpy / 1000,
            "entropy_kj_kgk": PropsSI("S", "P", pressure, "H", enthalpy, FLUID) / 1000,
            "quality": quality if 0 <= quality <= 1 else None}


def exchanger(d, side, pressure, h_low, h_high, mf, msw, sw, segments=48):
    """Counterflow finite-volume sizing, resolving saturation phase boundaries."""
    nodes = {h_low + (h_high - h_low) * i / segments for i in range(segments + 1)}
    for q in (0, 1):
        h = PropsSI("H", "P", pressure, "Q", q, FLUID)
        if h_low < h < h_high:
            nodes.add(h)
    nodes = sorted(nodes)
    u = getattr(d, "evaporator_u" if side == "warm" else "condenser_u")
    u_dirty = 1 / (1 / u + d.fouling_m2k_w)
    points = []
    for h in nodes:
        wf_t = PropsSI("T", "P", pressure, "H", h, FLUID) - 273.15
        # Along increasing working-fluid enthalpy, warm water approaches its inlet;
        # cold water approaches its outlet. Both circuits are countercurrent.
        sw_h = (sw["h_j_kg"] - mf / msw * (h_high - h) if side == "warm"
                else sw["h_j_kg"] + mf / msw * (h - h_low))
        sw_t = sw_temperature(sw["sa_g_kg"], sw_h)
        dt = sw_t - wf_t if side == "warm" else wf_t - sw_t
        points.append({"fraction": (h - h_low) / (h_high - h_low),
                       "working_fluid_c": wf_t, "seawater_c": sw_t, "approach_k": dt})
    pinch = min(x["approach_k"] for x in points)
    if pinch < d.minimum_pinch_k - 1e-7:
        raise ModelError(f"{'Evaporator' if side == 'warm' else 'Condenser'} pinch is {pinch:.3f} K; "
                         f"requires ≥ {d.minimum_pinch_k:g} K. Adjust cycle temperatures or seawater ΔT.")
    area = 0.0
    for i, (a, b) in enumerate(zip(points, points[1:])):
        x, y = a["approach_k"], b["approach_k"]
        lmtd = (x + y) / 2 if abs(x - y) < 1e-8 else (x - y) / log(x / y)
        area += mf * (nodes[i + 1] - nodes[i]) / (u_dirty * lmtd)
    return {"duty_w": mf * (h_high - h_low), "area_m2": area,
            "minimum_pinch_k": pinch, "effective_u_w_m2k": u_dirty, "profile": points}


def simulate(d=Design(), segments=48):
    d.validate()
    if not isinstance(segments, int) or not 8 <= segments <= 512:
        raise ModelError("Heat-exchanger segment count must be an integer from 8 to 512.")
    try:
        result = _simulate(d, segments)
        def check_finite(value):
            if isinstance(value, dict):
                for item in value.values():
                    check_finite(item)
            elif isinstance(value, list):
                for item in value:
                    check_finite(item)
            elif isinstance(value, (int, float)) and not isfinite(value):
                raise ModelError("Non-finite result: revise extreme inputs or out-of-range property conditions.")
        check_finite(result)
        return result
    except ModelError:
        raise
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        raise ModelError(f"Property calculation failed for this design: {exc}") from exc


def _simulate(d, segments):
    pl = PropsSI("P", "T", d.condenser_c + 273.15, "Q", 0, FLUID)
    ph = PropsSI("P", "T", d.evaporator_c + 273.15, "Q", 1, FLUID)
    h1 = PropsSI("H", "P", pl, "Q", 0, FLUID)
    s1 = PropsSI("S", "P", pl, "Q", 0, FLUID)
    h2s = PropsSI("H", "P", ph, "S", s1, FLUID)
    h2 = h1 + (h2s - h1) / d.wf_pump_eff
    h3 = PropsSI("H", "P", ph, "Q", 1, FLUID)
    s3 = PropsSI("S", "P", ph, "Q", 1, FLUID)
    h4s = PropsSI("H", "P", pl, "S", s3, FLUID)
    h4 = h3 - d.turbine_eff * (h3 - h4s)
    qh, qc, wt, wp = h3 - h2, h4 - h1, h3 - h4, h2 - h1
    if min(qh, qc, wt, wp) <= 0:
        raise ModelError("Cycle does not have positive heating, cooling, turbine work and pump work.")
    states = [state(1, "Condenser outlet", pl, h1), state(2, "Pump outlet", ph, h2),
              state(3, "Turbine inlet", ph, h3), state(4, "Turbine outlet", pl, h4)]
    quality = states[3]["quality"]
    if quality is not None and quality < d.minimum_quality:
        raise ModelError(f"Turbine exit quality {quality:.4f} is below the user criterion {d.minimum_quality:g}.")
    warm = seawater(d.warm_sp, d.warm_c, d.longitude, d.latitude)
    cold = seawater(d.cold_sp, d.cold_c, d.longitude, d.latitude)
    warm_out = seawater(d.warm_sp, d.warm_c - d.warm_drop_k, d.longitude, d.latitude)
    cold_out = seawater(d.cold_sp, d.cold_c + d.cold_rise_k, d.longitude, d.latitude)
    warm_ratio = qh / (warm["h_j_kg"] - warm_out["h_j_kg"])
    cold_ratio = qc / (cold_out["h_j_kg"] - cold["h_j_kg"])
    # Density at the mean exchanger temperature; constant-density pipe approximation.
    rw = seawater(d.warm_sp, d.warm_c - d.warm_drop_k / 2, d.longitude, d.latitude)["density_kg_m3"]
    rc = seawater(d.cold_sp, d.cold_c + d.cold_rise_k / 2, d.longitude, d.latitude)["density_kg_m3"]

    def power(mf):
        hw = hydraulics(d, "warm", mf * warm_ratio, rw)
        hc = hydraulics(d, "cold", mf * cold_ratio, rc)
        gross = mf * wt * d.generator_eff
        pump = mf * wp / d.wf_motor_eff
        aux = d.auxiliary_kw * 1000 + gross * d.auxiliary_fraction
        return gross - pump - hw["electrical_w"] - hc["electrical_w"] - aux, gross, pump, aux, hw, hc

    target = d.capacity_mw * 1e6
    if d.capacity_basis == "gross":
        mf = target / (wt * d.generator_eff)
    else:
        # Find the first increasing-branch root: fixed geometry makes friction
        # power grow approximately cubically with working-fluid flow.
        lo = 0.0
        hi = target / (wt * d.generator_eff)
        previous = -d.auxiliary_kw * 1000
        prior = previous_flow = 0.0
        for _ in range(180):
            current = power(hi)[0]
            if current >= target:
                break
            if current < previous:
                # Refine the local maximum so a root close to peak output is
                # not missed by the coarse 10% flow search (unimodal net curve).
                a, b = prior, hi
                for _ in range(65):
                    x, y = a + (b - a) / 3, b - (b - a) / 3
                    if power(x)[0] < power(y)[0]:
                        a = x
                    else:
                        b = y
                peak_flow = (a + b) / 2
                peak = power(peak_flow)[0]
                if peak >= target:
                    hi = peak_flow
                    break
                raise ModelError(f"Cannot deliver {d.capacity_mw:g} MW net with these pipes and losses. "
                                 f"Peak found ≈ {peak / 1e6:.3f} MW. "
                                 "Increase pipe diameter/count or reduce hydraulic losses.")
            prior = previous_flow
            previous_flow = hi
            previous = current
            hi *= 1.10
        else:
            raise ModelError("Could not bracket the target net capacity.")
        for _ in range(55):
            mid = (lo + hi) / 2
            if power(mid)[0] < target:
                lo = mid
            else:
                hi = mid
        mf = (lo + hi) / 2
    net, gross, pump, aux, hw, hc = power(mf)
    evap = exchanger(d, "warm", ph, h2, h3, mf, mf * warm_ratio, warm, segments)
    cond = exchanger(d, "cold", pl, h1, h4, mf, mf * cold_ratio, cold, segments)
    qin, qout = mf * qh, mf * qc
    turbine, pump_shaft = mf * wt, mf * wp
    cycle = turbine - pump_shaft
    residual = abs(qin - qout - cycle) / qin
    electrical_residual = abs(gross - pump - aux - hw["electrical_w"] - hc["electrical_w"] - net) / qin
    carnot = 1 - (d.cold_c + 273.15) / (d.warm_c + 273.15)
    cycle_carnot = 1 - (d.condenser_c + 273.15) / (d.evaporator_c + 273.15)
    # Entropy generation for each component using exact stream entropy changes.
    sf = [s["entropy_kj_kgk"] * 1000 for s in states]
    entropy = {"pump_w_k": mf * (sf[1] - sf[0]), "turbine_w_k": mf * (sf[3] - sf[2]),
               "evaporator_w_k": mf * (sf[2] - sf[1]) + mf * warm_ratio * (warm_out["s_j_kgk"] - warm["s_j_kgk"]),
               "condenser_w_k": mf * (sf[0] - sf[3]) + mf * cold_ratio * (cold_out["s_j_kgk"] - cold["s_j_kgk"])}
    if (residual > 1e-9 or electrical_residual > 1e-9
            or cycle / qin > cycle_carnot + 1e-8 or min(entropy.values()) < -1e-5):
        raise ModelError("Thermodynamic verification failed; inspect the input and property states.")
    warnings = ["Design-point sizing: exchanger areas are calculated requirements, not fixed installed equipment.",
                "Illustrative inputs; no experimental or site-specific validation has been performed."]
    if net <= 0:
        warnings.append("This gross-rated design consumes more electricity than it delivers.")
    for side, h in (("Warm", hw), ("Cold", hc)):
        if h["velocity_m_s"] > 3:
            warnings.append(f"{side} pipe velocity exceeds the illustrative 3 m/s screening level (not a code limit).")
        if 2300 < h["reynolds"] < 4000:
            warnings.append(f"{side} circuit is transitional; friction is an uncertain interpolation.")
    if quality is not None and quality < 1:
        warnings.append("Turbine exhaust is wet. The quality threshold is a user criterion; obtain vendor approval.")
    return {"model_version": VERSION, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "libraries": {"python": platform.python_version(), "CoolProp": CoolProp.__version__, "gsw": gsw.__version__},
            "inputs": asdict(d), "net_mw": net / 1e6, "gross_mw": gross / 1e6,
            "wf_mass_kg_s": mf, "turbine_mw": turbine / 1e6, "wf_pump_mw": pump / 1e6,
            "wf_pump_shaft_mw": pump_shaft / 1e6, "auxiliary_mw": aux / 1e6,
            "warm_pump_mw": hw["electrical_w"] / 1e6, "cold_pump_mw": hc["electrical_w"] / 1e6,
            "generator_loss_mw": (turbine - gross) / 1e6,
            "wf_motor_loss_mw": (pump - pump_shaft) / 1e6,
            "heat_input_mw": qin / 1e6, "heat_rejected_mw": qout / 1e6,
            "cycle_efficiency": cycle / qin, "net_efficiency": net / qin,
            "source_carnot_efficiency": carnot, "cycle_carnot_efficiency": cycle_carnot,
            "first_law_relative_residual": residual, "electrical_relative_residual": electrical_residual,
            "entropy_generation": entropy, "warm_outlet_c": d.warm_c - d.warm_drop_k,
            "cold_outlet_c": d.cold_c + d.cold_rise_k, "states": states,
            "warm_hydraulics": hw, "cold_hydraulics": hc, "evaporator": evap,
            "condenser": cond, "warnings": warnings}
