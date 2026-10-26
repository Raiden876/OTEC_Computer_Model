"""Depth-profile screening and explicit TEOS-10 intake-to-plant conversion."""
from bisect import bisect_left
from math import isfinite

import gsw

from otec import ModelError


def resource_scenario(data):
    """Interpolate one profile; assume reversible adiabatic ascent to 0 dbar.

    Profiles are scenarios, not observations unless independently supplied as such.
    No pipe heat transfer, salinity mixing, or bathymetric routing is modeled.
    """
    try:
        longitude, latitude = float(data["longitude"]), float(data["latitude"])
        warm_depth, cold_depth = float(data["warm_depth_m"]), float(data["cold_depth_m"])
        kind = data["temperature_type"]
        rows = [{k: float(row[k]) for k in ("depth_m", "temperature_c", "salinity_sp")}
                for row in data["profile"]]
    except (ValueError, TypeError, KeyError) as exc:
        raise ModelError("Profile requires depth_m, temperature_c and salinity_sp, plus intake depths and coordinates.") from exc
    if not 2 <= len(rows) <= 500:
        raise ModelError("Provide 2–500 profile levels.")
    if kind not in ("potential_0dbar", "in_situ"):
        raise ModelError("Declare profile temperatures as potential_0dbar or in_situ.")
    values = [longitude, latitude, warm_depth, cold_depth] + [v for row in rows for v in row.values()]
    if not all(isfinite(v) for v in values):
        raise ModelError("Ocean-profile inputs must be finite.")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ModelError("Invalid ocean-profile coordinates.")
    rows.sort(key=lambda row: row["depth_m"])
    depths = [r["depth_m"] for r in rows]
    if len(set(depths)) != len(depths):
        raise ModelError("Profile depths must be unique.")
    for row in rows:
        if not (0 <= row["depth_m"] <= 12000 and -2 <= row["temperature_c"] <= 40
                and 0 <= row["salinity_sp"] <= 42):
            raise ModelError("Profile limits: depth 0–12000 m, temperature −2–40 °C, SP 0–42.")
    if not depths[0] <= warm_depth < cold_depth <= depths[-1]:
        raise ModelError("Both intakes must lie inside the profile, with warm intake shallower than cold intake. No extrapolation.")

    def sample(depth):
        i = bisect_left(depths, depth)
        if i < len(rows) and depths[i] == depth:
            t, sp = rows[i]["temperature_c"], rows[i]["salinity_sp"]
        else:
            left, right = rows[i - 1], rows[i]
            fraction = (depth - left["depth_m"]) / (right["depth_m"] - left["depth_m"])
            t = left["temperature_c"] + fraction * (right["temperature_c"] - left["temperature_c"])
            sp = left["salinity_sp"] + fraction * (right["salinity_sp"] - left["salinity_sp"])
        p = float(gsw.p_from_z(-depth, latitude))
        sa = float(gsw.SA_from_SP(sp, p, longitude, latitude))
        if kind == "in_situ":
            insitu = t
            plant_t = float(gsw.pt0_from_t(sa, t, p))
        else:
            plant_t = t
            ct = float(gsw.CT_from_pt(sa, t))
            insitu = float(gsw.t_from_CT(sa, ct, p))
        if insitu <= float(gsw.t_freezing(sa, p, 0)):
            raise ModelError("Selected intake is at or below the seawater freezing temperature.")
        # Preserve intake Absolute Salinity when the main model converts SP at
        # plant pressure. This is an equivalent plant SP, not a measured sample.
        plant_sp = float(gsw.SP_from_SA(sa, 0, longitude, latitude))
        out = {"depth_m": depth, "profile_temperature_c": t, "practical_salinity": sp,
               "pressure_dbar": p, "absolute_salinity_g_kg": sa,
               "ocean_in_situ_c": insitu, "plant_inlet_c": plant_t, "plant_equivalent_sp": plant_sp}
        if not all(isfinite(v) for v in out.values()):
            raise ModelError("TEOS-10 could not resolve this profile location.")
        return out

    warm, cold = sample(warm_depth), sample(cold_depth)
    return {"profile": rows, "temperature_type": kind, "longitude": longitude, "latitude": latitude,
            "warm_depth_m": warm_depth, "cold_depth_m": cold_depth, "warm": warm, "cold": cold,
            "plant_delta_k": warm["plant_inlet_c"] - cold["plant_inlet_c"],
            "assumption": "Linear depth interpolation; reversible adiabatic ascent to 0 dbar; no intake heat gain or mixing.",
            "provenance": "User-supplied scenario. Validate source metadata before interpreting as observations."}
