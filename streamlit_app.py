"""Professional local OTEC sandbox. Launch with .venv/bin/python run.py."""
import csv
from dataclasses import asdict
from bisect import bisect_left
from hashlib import sha256
from io import StringIO
import json
from math import isfinite
from pathlib import Path

import streamlit as st

from app import GROUPS
from charts import (capacity_comparison, cycle_state_diagram, electrical_waterfall, exchanger_sizing,
                    heat_transfer_profile, hydraulic_flows, hydraulic_heads, ocean_profile as ocean_profile_chart)
from ocean import resource_scenario
from otec import Design, ModelError, simulate
from views import metrics, number as n, table

ROOT = Path(__file__).parent
DEFAULT_PROFILE_ROWS = [
    {"depth_m": 10, "temperature_c": 28, "salinity_sp": 35},
    {"depth_m": 100, "temperature_c": 24, "salinity_sp": 35},
    {"depth_m": 200, "temperature_c": 18, "salinity_sp": 35},
    {"depth_m": 500, "temperature_c": 9, "salinity_sp": 35},
    {"depth_m": 1000, "temperature_c": 5, "salinity_sp": 35},
    {"depth_m": 1500, "temperature_c": 4, "salinity_sp": 35},
]
st.set_page_config(page_title="OTEC | Engineering Sandbox", page_icon="🌊", layout="wide")
st.html(f"<style>{ROOT.joinpath('streamlit_theme.css').read_text()}</style>")


def set_widgets(data):
    for key, value in data.items():
        if key == "capacity_mw":
            continue
        st.session_state["d_" + key] = value if key == "capacity_basis" else float(value)
    capacity_mw = float(data["capacity_mw"])
    unit = "MW" if capacity_mw >= 1 else "kW"
    st.session_state.d_capacity_unit = unit
    st.session_state.d_capacity_value = capacity_mw if unit == "MW" else capacity_mw * 1000
    st.session_state._capacity_unit_previous = unit


def capacity_to_mw(value, unit):
    return float(value) / 1000 if unit == "kW" else float(value)


def capacity_label(capacity_mw):
    return f"{capacity_mw * 1000:g} kW" if capacity_mw < 1 else f"{capacity_mw:g} MW"


def capacity_unit_changed():
    previous = st.session_state.get("_capacity_unit_previous", st.session_state.d_capacity_unit)
    current = st.session_state.d_capacity_unit
    if previous != current and st.session_state.get("d_capacity_value") is not None:
        factor = 1000 if previous == "MW" else .001
        st.session_state.d_capacity_value = float(st.session_state.d_capacity_value) * factor
    st.session_state._capacity_unit_previous = current


def capacity_value_changed():
    value = st.session_state.get("d_capacity_value")
    if value is None:
        return
    capacity_mw = capacity_to_mw(value, st.session_state.d_capacity_unit)
    if not isfinite(capacity_mw) or not .01 <= capacity_mw <= 100:
        return
    applied = st.session_state.get("applied", {})
    geometry = (applied if abs(capacity_mw - applied.get("capacity_mw", -1)) < 1e-12
                else asdict(Design.preset(capacity_mw)))
    for key in ("warm_diameter_m", "cold_diameter_m", "warm_pipes", "cold_pipes"):
        st.session_state["d_" + key] = float(geometry[key])


def draft_number(value):
    return f"{value:g}" if isinstance(value, (int, float)) else "—"


def source_matches(design, resource):
    if not resource:
        return False
    expected = {"warm_c": resource["warm"]["plant_inlet_c"], "cold_c": resource["cold"]["plant_inlet_c"],
                "warm_sp": resource["warm"]["plant_equivalent_sp"], "cold_sp": resource["cold"]["plant_equivalent_sp"],
                "longitude": resource["longitude"], "latitude": resource["latitude"],
                "warm_intake_depth_m": resource["warm_depth_m"], "cold_intake_depth_m": resource["cold_depth_m"]}
    return all(abs(design[k] - v) < 1e-8 for k, v in expected.items())


def solve_design(data, resource=None):
    result = simulate(Design.from_dict(data))
    if source_matches(result["inputs"], resource):
        result["ocean_resource"] = resource
    return result


def stage_design(data, resource=None):
    """Queue widget changes for the start of the next rerun, before creation."""
    pending = {"draft": data, "resource": resource}
    try:
        pending["result"] = solve_design(data, resource)
        pending["error"] = ""
    except ModelError as exc:
        pending["error"] = str(exc)
    st.session_state.pending = pending


def reset_design():
    stage_design(asdict(Design()))


def interactive_chart(spec, key, description):
    # Include the applied-result revision and inline data so charts remount on Apply.
    result_revision = st.session_state.get("result_revision", 0)
    fingerprint = sha256(json.dumps({"revision": result_revision, "spec": spec},
                                    sort_keys=True, separators=(",", ":"),
                                    default=str).encode("utf-8")).hexdigest()[:12]
    st.vega_lite_chart(spec=spec, width="stretch", key=f"{key}_{fingerprint}", alt=description)


def profile_editor_rows():
    """Read the latest editor delta when Apply runs, including unrendered edits."""
    rows = [dict(row) for row in st.session_state.d_ocean_profile_rows]
    edits = st.session_state.get("d_ocean_profile_editor", {})
    for index, values in edits.get("edited_rows", {}).items():
        rows[int(index)].update(values)
    deleted = {int(index) for index in edits.get("deleted_rows", [])}
    rows = [row for index, row in enumerate(rows) if index not in deleted]
    rows.extend(dict(row) for row in edits.get("added_rows", []))
    return rows


def apply_design():
    """Calculate before the rerun, so every output receives the same new result."""
    data = {key: st.session_state["d_" + key] for key in st.session_state.applied
            if key != "capacity_mw"}
    data["capacity_mw"] = capacity_to_mw(st.session_state.d_capacity_value,
                                         st.session_state.d_capacity_unit)
    try:
        resource = resource_scenario({
            "profile": profile_editor_rows(),
            "temperature_type": st.session_state.d_ocean_profile_type,
            "longitude": data["longitude"], "latitude": data["latitude"],
            "warm_depth_m": data["warm_intake_depth_m"],
            "cold_depth_m": data["cold_intake_depth_m"],
        })
        resource["source_label"] = st.session_state.d_ocean_profile_name or "User-edited ocean profile"
        warm_c = resource["warm"]["plant_inlet_c"]
        cold_c = resource["cold"]["plant_inlet_c"]
        if st.session_state.d_track_cycle:
            # Explicit cycle edits take priority over automatic source tracking.
            applied = st.session_state.applied
            if abs(data["evaporator_c"] - applied["evaporator_c"]) < 1e-10:
                data["evaporator_c"] += warm_c - data["warm_c"]
            if abs(data["condenser_c"] - applied["condenser_c"]) < 1e-10:
                data["condenser_c"] += cold_c - data["cold_c"]
        data.update(warm_c=warm_c, cold_c=cold_c,
                    warm_sp=resource["warm"]["plant_equivalent_sp"],
                    cold_sp=resource["cold"]["plant_equivalent_sp"])
        result = solve_design(data, resource)
        st.session_state.pending = {
            "draft": result["inputs"], "resource": resource,
            "result": result, "error": "", "track_cycle": st.session_state.d_track_cycle,
            "just_applied": True,
        }
    except ModelError as exc:
        st.session_state.error = str(exc)


def profile_plot_data(rows, warm_depth, cold_depth, temperature_type, longitude, latitude, source_label):
    """Prepare raw-profile chart data without requiring a thermodynamic solve."""
    points = []
    for row in rows:
        try:
            depth = float(row["depth_m"])
            temperature = float(row["temperature_c"])
        except (ValueError, TypeError, KeyError) as exc:
            raise ModelError("Depth and temperature values must be numbers to plot this profile.") from exc
        if not isfinite(depth) or not isfinite(temperature) or not 0 <= depth <= 12000:
            raise ModelError("Plot requires finite temperatures and depths from 0 to 12,000 m.")
        points.append({"depth_m": depth, "temperature_c": temperature,
                       "salinity_sp": row.get("salinity_sp", "")})
    points.sort(key=lambda point: point["depth_m"])
    depths = [point["depth_m"] for point in points]
    if len(points) < 2 or len(set(depths)) != len(points) or depths[0] == depths[-1]:
        raise ModelError("Provide at least two distinct profile depths to draw the temperature curve.")

    def sample(depth):
        if depth < depths[0] or depth > depths[-1]:
            return None
        i = bisect_left(depths, depth)
        if i < len(points) and depths[i] == depth:
            return points[i]["temperature_c"]
        left, right = points[i - 1], points[i]
        f = (depth - left["depth_m"]) / (right["depth_m"] - left["depth_m"])
        return left["temperature_c"] + f * (right["temperature_c"] - left["temperature_c"])

    return {"profile": points, "temperature_type": temperature_type,
            "longitude": longitude, "latitude": latitude, "source_label": source_label,
            "warm": {"depth_m": warm_depth, "profile_temperature_c": sample(warm_depth)},
            "cold": {"depth_m": cold_depth, "profile_temperature_c": sample(cold_depth)}}


def as_csv(rows):
    buf = StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()


def engineering_csv(r):
    rows = [["OTEC model version", r["model_version"]], ["Calculated UTC", r["timestamp_utc"]],
            ["Input", "Value"], *r["inputs"].items(), [], ["Metric", "Value"],
            *[(key, value) for key, value in r.items() if isinstance(value, (int, float))], [],
            ["State", "T_C", "p_bar", "h_kJ_kg", "s_kJ_kgK", "quality"],
            *[(s["state"], s["temperature_c"], s["pressure_bar"], s["enthalpy_kj_kg"], s["entropy_kj_kgk"], s["quality"])
              for s in r["states"]], [], ["Hydraulic metric", "Warm", "Cold"],
            *[(key, value, r["cold_hydraulics"][key]) for key, value in r["warm_hydraulics"].items()], [],
            ["Exchanger", "Duty_W", "Area_m2", "Minimum_pinch_K"],
            *[(key, r[key]["duty_w"], r[key]["area_m2"], r[key]["minimum_pinch_k"]) for key in ("evaporator", "condenser")],
            [], ["Component entropy generation", "W_K"], *r["entropy_generation"].items(),
            [], ["Ocean_resource_JSON", json.dumps(r.get("ocean_resource"), allow_nan=False)]]
    return as_csv(rows)


if "applied" not in st.session_state:
    st.session_state.applied = asdict(Design())
    st.session_state.result = simulate()
    st.session_state.resource = None
    st.session_state.error = ""
    st.session_state.comparison = None
    st.session_state.ocean_profile = DEFAULT_PROFILE_ROWS
    st.session_state.ocean_profile_type = "potential_0dbar"
    st.session_state.ocean_profile_name = "Illustrative tropical profile"
    st.session_state.d_ocean_profile_rows = [dict(row) for row in DEFAULT_PROFILE_ROWS]
    st.session_state.d_ocean_profile_type = "potential_0dbar"
    st.session_state.d_ocean_profile_name = "Illustrative tropical profile"
    st.session_state.d_track_cycle = True
    set_widgets(st.session_state.applied)

if "result_revision" not in st.session_state:
    st.session_state.result_revision = 0

if "d_capacity_value" not in st.session_state or "d_capacity_unit" not in st.session_state:
    set_widgets(st.session_state.applied)
if "ocean_profile" not in st.session_state:
    st.session_state.ocean_profile = DEFAULT_PROFILE_ROWS
    st.session_state.ocean_profile_type = "potential_0dbar"
    st.session_state.ocean_profile_name = "Illustrative tropical profile"
if "d_ocean_profile_rows" not in st.session_state:
    st.session_state.d_ocean_profile_rows = [dict(row) for row in st.session_state.ocean_profile]
    st.session_state.d_ocean_profile_type = st.session_state.ocean_profile_type
    st.session_state.d_ocean_profile_name = st.session_state.ocean_profile_name
if "d_track_cycle" not in st.session_state:
    st.session_state.d_track_cycle = True

if "pending" in st.session_state:
    pending = st.session_state.pop("pending")
    set_widgets(pending["draft"])
    st.session_state.resource = pending["resource"]
    st.session_state.error = pending["error"]
    st.session_state.comparison = None
    if pending["resource"]:
        st.session_state.ocean_profile = pending["resource"]["profile"]
        st.session_state.ocean_profile_type = pending["resource"]["temperature_type"]
        st.session_state.ocean_profile_name = pending["resource"].get("source_label", "Loaded scenario profile")
        st.session_state.d_ocean_profile_rows = [dict(row) for row in pending["resource"]["profile"]]
        st.session_state.d_ocean_profile_type = pending["resource"]["temperature_type"]
        st.session_state.d_ocean_profile_name = st.session_state.ocean_profile_name
        st.session_state.pop("d_ocean_profile_editor", None)
    else:
        st.session_state.ocean_profile = DEFAULT_PROFILE_ROWS
        st.session_state.ocean_profile_type = "potential_0dbar"
        st.session_state.ocean_profile_name = "Illustrative tropical profile"
        st.session_state.d_ocean_profile_rows = [dict(row) for row in DEFAULT_PROFILE_ROWS]
        st.session_state.d_ocean_profile_type = "potential_0dbar"
        st.session_state.d_ocean_profile_name = "Illustrative tropical profile"
        st.session_state.pop("d_ocean_profile_editor", None)
    st.session_state.d_track_cycle = pending.get("track_cycle", True)
    if "result" in pending:
        st.session_state.result = pending["result"]
        st.session_state.applied = pending["result"]["inputs"]
        st.session_state.result_revision += 1
    if pending.get("just_applied"):
        st.session_state.just_applied = True


with st.sidebar:
    st.html('<div class="eyebrow">Plant engineering</div><div class="section-title">Design configuration</div>')
    st.caption("Choose any target from 10 kW to 100 MW. Changing capacity also applies the illustrative pipe-sizing rule.")
    capacity_unit = st.session_state.d_capacity_unit
    capacity_value_col, capacity_unit_col = st.columns([1.4, .8])
    with capacity_value_col:
        st.number_input("Target electrical capacity", key="d_capacity_value",
                         min_value=10.0 if capacity_unit == "kW" else .01,
                         max_value=100000.0 if capacity_unit == "kW" else 100.0,
                         step=1.0 if capacity_unit == "kW" else .001,
                         format="%.1f" if capacity_unit == "kW" else "%.4f",
                         on_change=capacity_value_changed)
    with capacity_unit_col:
        st.selectbox("Unit", ["kW", "MW"], key="d_capacity_unit", on_change=capacity_unit_changed)
    st.selectbox("Capacity definition", ["net", "gross"], key="d_capacity_basis",
                 format_func=lambda x: "Net after pumps & auxiliaries" if x == "net" else "Gross generator output")
    profile_rows_draft = st.session_state.d_ocean_profile_rows
    with st.expander("Ocean source & intake settings", expanded=True):
        st.number_input("Warm-water intake depth · m", key="d_warm_intake_depth_m",
                        min_value=0.0, max_value=12000.0, step=1.0, format="%.0f")
        st.number_input("Cold-water intake depth · m", key="d_cold_intake_depth_m",
                        min_value=0.0, max_value=12000.0, step=10.0, format="%.0f")
        coordinate_cols = st.columns(2)
        with coordinate_cols[0]:
            st.number_input("Longitude · °E", key="d_longitude", min_value=-180.0,
                            max_value=180.0, step=0.1, format="%.2f")
        with coordinate_cols[1]:
            st.number_input("Latitude · °N", key="d_latitude", min_value=-90.0,
                            max_value=90.0, step=0.1, format="%.2f")
        st.text_input("Profile label", key="d_ocean_profile_name", max_chars=120)
        st.selectbox("Profile temperature type", ["potential_0dbar", "in_situ"],
                     key="d_ocean_profile_type",
                     format_func=lambda value: "Potential temperature · θ₀" if value == "potential_0dbar" else "In-situ temperature at depth")
        st.caption("This profile is the single ocean source for both the graph and design. Edit or add levels below.")
        profile_rows_draft = st.data_editor(
            st.session_state.d_ocean_profile_rows,
            key="d_ocean_profile_editor",
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            height=235,
            column_config={
                "depth_m": st.column_config.NumberColumn("Depth · m", min_value=0, max_value=12000, step=10, format="%.0f"),
                "temperature_c": st.column_config.NumberColumn("Temperature · °C", min_value=-2, max_value=40, step=0.1, format="%.2f"),
                "salinity_sp": st.column_config.NumberColumn("Salinity · SP", min_value=0, max_value=42, step=0.1, format="%.2f"),
            },
        )
        st.checkbox("Keep current inlet-to-cycle temperature margins", key="d_track_cycle",
                    help="When an inlet changes, shift its cycle temperature by the same amount. Explicit edits to evaporation or condensation take priority.")
        st.caption("Both intake depths must fall within the profile. Temperature and salinity are interpolated between levels.")
    ocean_inputs = {"warm_c", "cold_c", "warm_sp", "cold_sp", "longitude", "latitude",
                    "warm_intake_depth_m", "cold_intake_depth_m"}
    visible_group_index = 0
    for title, controls in GROUPS:
        visible_controls = [control for control in controls if control[0] not in ocean_inputs]
        if not visible_controls:
            continue
        with st.expander(title, expanded=(visible_group_index == 0)):
            cols = st.columns(2)
            for i, (key, label, step, help_text) in enumerate(visible_controls):
                fmt = "%.5f" if step < .001 else "%.3f" if step < .01 else "%.2f" if step < 1 else "%.0f"
                with cols[i % 2]:
                    st.number_input(label, key="d_" + key, step=float(step), format=fmt, help=help_text)
        visible_group_index += 1
    draft = {key: st.session_state.get("d_" + key) for key in st.session_state.applied
             if key != "capacity_mw"}
    if hasattr(profile_rows_draft, "to_dict"):
        profile_rows_for_preview = profile_rows_draft.to_dict(orient="records")
    else:
        profile_rows_for_preview = profile_rows_draft
    try:
        ocean_draft = resource_scenario({
            "profile": profile_rows_for_preview,
            "temperature_type": st.session_state.d_ocean_profile_type,
            "longitude": draft["longitude"], "latitude": draft["latitude"],
            "warm_depth_m": draft["warm_intake_depth_m"],
            "cold_depth_m": draft["cold_intake_depth_m"],
        })
        draft_warm, draft_cold = ocean_draft["warm"]["plant_inlet_c"], ocean_draft["cold"]["plant_inlet_c"]
        draft_warm_sp, draft_cold_sp = ocean_draft["warm"]["plant_equivalent_sp"], ocean_draft["cold"]["plant_equivalent_sp"]
        st.caption(f"Profile-derived draft · warm {draft_number(draft_warm)} / cold {draft_number(draft_cold)} °C · "
                   f"SP {draft_number(draft_warm_sp)}/{draft_number(draft_cold_sp)} · "
                   f"{draft_number(draft['latitude'])}° N, {draft_number(draft['longitude'])}° E")
    except (ModelError, TypeError, ValueError) as exc:
        st.caption(f"Profile draft needs attention: {exc}")
    st.caption("Changes stay in draft until you select Apply edits & calculate below.")
    st.button("Apply edits & calculate", type="primary", width="stretch",
              key="calculate_design", on_click=apply_design)
    applied = st.session_state.applied
    if st.session_state.pop("just_applied", False):
        st.success(f"Applied · {capacity_label(applied['capacity_mw'])} {applied['capacity_basis']} · "
                   f"{applied['warm_c']:g} / {applied['cold_c']:g} °C")
    elif st.session_state.error:
        st.error(f"Edits not applied: {st.session_state.error}")
    else:
        st.caption(f"Currently applied · {capacity_label(applied['capacity_mw'])} {applied['capacity_basis']} · "
                   f"warm {applied['warm_c']:g} °C / cold {applied['cold_c']:g} °C · "
                   f"SP {applied['warm_sp']:g}/{applied['cold_sp']:g} · "
                   f"{applied['latitude']:g}° N, {applied['longitude']:g}° E")
    st.button("Reset to 1 MW reference", key="reset_design", on_click=reset_design, width="stretch")
    with st.expander("Save or load a scenario"):
        applied = st.session_state.applied
        snapshot = {"inputs": applied, "ocean_resource": st.session_state.result.get("ocean_resource")}
        st.download_button("Save applied scenario", json.dumps(snapshot, indent=2), "otec-scenario.json", "application/json", on_click="ignore")
        loaded_file = st.file_uploader("Load scenario JSON", type=["json"], key="scenario_file")
        if st.button("Load selected scenario", disabled=loaded_file is None):
            try:
                if loaded_file.size > 65536:
                    raise ModelError("Scenario file exceeds 64 KB.")
                loaded = json.loads(loaded_file.getvalue())
                if not isinstance(loaded, dict):
                    raise ModelError("Expected a JSON object.")
                design = Design.from_dict(loaded.get("inputs", loaded))
                resource = loaded.get("ocean_resource")
                if resource:
                    verified = resource_scenario(resource)
                    verified["source_label"] = str(resource.get("source_label", "Imported scenario"))[:120]
                    resource = verified
                stage_design(asdict(design), resource)
                st.rerun()
            except (ValueError, TypeError, KeyError) as exc:
                st.error(f"Cannot load this scenario: {exc}")
    st.html('<div class="sidebar-foot">R717 working fluid · TEOS-10 seawater<br>Local computation · no dataset downloads<br>Numerical verification ≠ experimental validation</div>')

st.html('''<div class="masthead"><div class="brand"><div class="brand-symbol">≋</div><div><h1>OTEC <span>/ Engineering Sandbox</span></h1><p>OCEAN RESOURCE → THERMODYNAMIC CYCLE → NET ELECTRICAL OUTPUT</p></div></div><div class="masthead-pill">LOCAL WORKSPACE · RESEARCH v0.1</div></div>''')

if st.session_state.error:
    st.error(st.session_state.error)
    st.warning("The requested design is infeasible. Results below show the previous valid design; result downloads and capacity comparison are disabled until a new design solves.")

profile_rows = profile_rows_draft
if hasattr(profile_rows, "to_dict"):
    profile_rows = profile_rows.to_dict(orient="records")
profile_type = st.session_state.d_ocean_profile_type
profile_label = st.session_state.d_ocean_profile_name or "User-edited ocean profile"
warm_depth = float(st.session_state.d_warm_intake_depth_m)
cold_depth = float(st.session_state.d_cold_intake_depth_m)
longitude = float(st.session_state.d_longitude)
latitude = float(st.session_state.d_latitude)
profile_preview, profile_error = None, ""
try:
    profile_preview = resource_scenario({
        "profile": profile_rows, "temperature_type": profile_type,
        "longitude": longitude, "latitude": latitude,
        "warm_depth_m": warm_depth, "cold_depth_m": cold_depth,
    })
    profile_preview["source_label"] = profile_label
except ModelError as exc:
    profile_error = str(exc)

try:
    profile_depths = [float(row["depth_m"]) for row in profile_rows]
except (TypeError, ValueError, KeyError):
    profile_depths = []
profile_plot = None
if len(profile_depths) >= 2:
    try:
        profile_plot = profile_plot_data(profile_rows, warm_depth, cold_depth, profile_type,
                                         longitude, latitude, profile_label)
    except ModelError as exc:
        if not profile_error:
            profile_error = str(exc)
profile_chart_data = profile_preview or profile_plot
profile_chart_key = "ocean_profile_" + str(hash(json.dumps(
    {"profile": profile_rows, "warm_depth": warm_depth, "cold_depth": cold_depth},
    sort_keys=True, default=str)))

with st.container(border=True):
    st.html('<div class="eyebrow">01 / Ocean resource</div><div class="section-title">Ocean source profile</div><div class="section-caption">One editable profile in the sidebar drives this graph and the derived plant inlet conditions. The button below the sidebar controls applies the complete draft.</div>')
    profile_col, summary_col = st.columns([1.3, 1], gap="large")
    with profile_col:
        if profile_chart_data:
            interactive_chart(ocean_profile_chart(profile_chart_data), profile_chart_key,
                              "Interactive ocean temperature profile with the selected warm- and cold-water intake depths. Hover for values; drag or scroll to zoom.")
            st.caption("Live preview from the single sidebar profile · hover for depth, temperature and salinity · drag or scroll to zoom.")
        else:
            st.info("Provide at least two unique, valid profile levels to draw the ocean curve.")
        st.caption(f"{profile_label} · {profile_type.replace('_', ' ')} · {latitude:g}° N, {longitude:g}° E")
    with summary_col:
        st.markdown("#### Selected-depth preview")
        st.caption("Profile values follow your sidebar edits. They affect the model only after you apply the draft.")
        if profile_preview:
            st.html(f'<div class="intake-grid"><div class="intake-card"><span>WARM INTAKE · {warm_depth:,.0f} m</span><strong>{n(profile_preview["warm"]["profile_temperature_c"],2)} °C</strong><small>Plant inlet after ascent · {n(profile_preview["warm"]["plant_inlet_c"],2)} °C</small></div><div class="intake-card cold"><span>COLD INTAKE · {cold_depth:,.0f} m</span><strong>{n(profile_preview["cold"]["profile_temperature_c"],2)} °C</strong><small>Plant inlet after ascent · {n(profile_preview["cold"]["plant_inlet_c"],2)} °C</small></div></div>')
            if (source_matches(st.session_state.applied, profile_preview)
                    and source_matches(st.session_state.applied, st.session_state.resource)):
                st.success("Profile conditions match the applied design.")
            else:
                st.info("These profile conditions are a draft. Click Apply edits & calculate in the sidebar to use them.")
            st.dataframe([{"Depth (m)": row["depth_m"], "Profile temperature (°C)": row["temperature_c"],
                           "Practical Salinity (SP)": row["salinity_sp"]} for row in profile_rows],
                         hide_index=True, width="stretch", height=230)
        else:
            st.warning(profile_error or "The selected profile conditions are unavailable.")

r = st.session_state.result
d = r["inputs"]
header, compare_button = st.columns([2.7, 1], vertical_alignment="bottom")
with header:
    st.html(f'<div class="eyebrow" style="margin-top:20px">02 / Plant sizing & performance</div><div class="section-title">{d["capacity_mw"]:g} MW {d["capacity_basis"]} electrical design</div><div class="section-caption">R717 · {d["warm_c"]:.2f} °C warm / {d["cold_c"]:.2f} °C cold · {r["wf_mass_kg_s"]:,.1f} kg/s ammonia</div>')
with compare_button:
    if st.button("Compare reference sizes", width="stretch", key="compare_capacities", disabled=bool(st.session_state.error)):
        compared = []
        for capacity in (1, 10, 25, 50):
            data = dict(d)
            preset = asdict(Design.preset(capacity))
            for key in ("capacity_mw", "warm_diameter_m", "cold_diameter_m", "warm_pipes", "cold_pipes"):
                data[key] = preset[key]
            try:
                compared.append({"result": solve_design(data, st.session_state.resource), "capacity": capacity})
            except ModelError as exc:
                compared.append({"capacity": capacity, "error": str(exc)})
        st.session_state.comparison = compared

if st.session_state.comparison:
    with st.container(border=True):
        st.markdown("#### Capacity comparison")
        rows = []
        export = [["target_mw", "basis", "net_mw", "gross_mw", "ammonia_flow_kg_s", "warm_flow_m3_s", "cold_flow_m3_s", "evaporator_area_m2", "condenser_area_m2", "inputs_json", "error"]]
        for case in st.session_state.comparison:
            if "result" in case:
                c = case["result"]
                rows.append([f'{case["capacity"]} MW', n(c["net_mw"]), n(c["gross_mw"]), n(c["wf_mass_kg_s"], 1),
                             f'{n(c["warm_hydraulics"]["volume_m3_s"],2)} / {n(c["cold_hydraulics"]["volume_m3_s"],2)}',
                             n((c["evaporator"]["area_m2"] + c["condenser"]["area_m2"]) / 1000, 1),
                             f'{int(c["inputs"]["cold_pipes"])} × {c["inputs"]["cold_diameter_m"]:.2f} m'])
                export.append([case["capacity"], c["inputs"]["capacity_basis"], c["net_mw"], c["gross_mw"], c["wf_mass_kg_s"],
                               c["warm_hydraulics"]["volume_m3_s"], c["cold_hydraulics"]["volume_m3_s"],
                               c["evaporator"]["area_m2"], c["condenser"]["area_m2"], json.dumps(c["inputs"]), ""])
            else:
                rows.append([f'{case["capacity"]} MW', "Infeasible", "—", "—", "—", "—", "—"])
                export.append([case["capacity"], d["capacity_basis"], "", "", "", "", "", "", "", "", case["error"]])
                st.warning(f'{case["capacity"]} MW: {case["error"]}')
        if any("result" in case for case in st.session_state.comparison):
            power_comparison, flow_comparison, area_comparison = st.tabs(["Gross & net electricity", "Seawater requirements", "Heat-exchanger area"])
            for tab, metric in ((power_comparison, "power"), (flow_comparison, "flow"), (area_comparison, "area")):
                with tab:
                    interactive_chart(capacity_comparison(st.session_state.comparison, metric), "capacity_" + metric,
                                      "Comparison of the feasible 1, 10, 25 and 50 MW designs; " + metric + " quantities shown on a common scale.")
        with st.expander("Comparison values and pipe layouts"):
            st.html(table(["Target", "Net · MW", "Gross · MW", "Ammonia · kg/s", "Warm / cold · m³/s", "HX area · 10³ m²", "Pipes / circuit"], rows))
        st.caption("Independent design sizing at the same source conditions. The 1, 10, 25, and 50 MW reference cases use the illustrative pipe-scaling rule and newly sized exchangers; these are not operating points of the same installed plant.")
        st.download_button("Export comparison CSV", as_csv(export), "otec-capacity-comparison.csv", "text/csv", on_click="ignore")

st.html(metrics(r))
with st.container(border=True, key=f"plant_results_{st.session_state.result_revision}"):
    st.html(f'<div class="flow-grid"><div class="flow-node"><strong>① Condenser outlet</strong>Saturated liquid · {r["states"][0]["pressure_bar"]:.2f} bar</div><div class="flow-node"><strong>② Pump → evaporator</strong>Heat input · {r["heat_input_mw"]:,.2f} MWₜₕ</div><div class="flow-node"><strong>③ Vapor → turbine</strong>Shaft output · {r["turbine_mw"]:,.3f} MW</div><div class="flow-node"><strong>④ Turbine → condenser</strong>Heat rejection · {r["heat_rejected_mw"]:,.2f} MWₜₕ</div></div>')
    power_tab, cycle_tab, hydraulic_tab, checks_tab, basis_tab = st.tabs(["Power & heat", "Thermodynamic cycle", "Hydraulics", "Verification", "Model basis"],
                                                                    key="performance_tabs", on_change="rerun")
    with power_tab:
        left, right = st.columns(2, gap="large")
        with left:
            st.markdown("#### From gross generation to net output")
            if st.session_state.error:
                st.warning("Showing the previous valid result because the latest edits were rejected: "
                           + st.session_state.error)
            interactive_chart(electrical_waterfall(r), "electrical_waterfall",
                              "Gross output, pump and auxiliary deductions, and net electrical output in MW.")
            st.html(table(["Plant requirement", "Value"], [["Ammonia circulation · kg/s", n(r["wf_mass_kg_s"], 1)]]))
            st.caption("Ammonia circulates in a closed loop. This is the flow rate for the applied design, not the total ammonia charge; the model does not calculate inventory. Draft edits apply after calculation.")
        with right:
            st.markdown("#### Required heat exchangers")
            area_tab, duty_tab = st.tabs(["Surface area", "Thermal duty"], key="exchanger_tabs", on_change="rerun")
            with area_tab:
                interactive_chart(exchanger_sizing(r, "area"), "exchanger_area", "Required evaporator and condenser surface area in square metres.")
            with duty_tab:
                interactive_chart(exchanger_sizing(r, "duty"), "exchanger_duty", "Evaporator heat input and condenser heat rejection in thermal MW.")
            with st.expander("Heat-exchanger values", key="hx_values", on_change="rerun"):
                st.html(table(["Sizing result", "Evaporator", "Condenser"],
                          [["Duty · MWₜₕ", n(r["evaporator"]["duty_w"] / 1e6, 2), n(r["condenser"]["duty_w"] / 1e6, 2)],
                           ["Effective area · m²", n(r["evaporator"]["area_m2"], 0), n(r["condenser"]["area_m2"], 0)],
                           ["Minimum pinch · K", n(r["evaporator"]["minimum_pinch_k"], 2), n(r["condenser"]["minimum_pinch_k"], 2)],
                           ["Fouled U · W/(m² K)", n(r["evaporator"]["effective_u_w_m2k"], 0), n(r["condenser"]["effective_u_w_m2k"], 0)]]))
            st.caption("Calculated required area at specified U and outlet temperatures. Exchanger pressure drop remains an independent assumption.")
        with st.expander("Countercurrent heat-transfer profiles", expanded=True,
                         key="hx_profiles", on_change="rerun"):
            for col, name in zip(st.columns(2), ("evaporator", "condenser")):
                with col:
                    st.markdown(f"#### {name.capitalize()}")
                    interactive_chart(heat_transfer_profile(r, name), name + "_profile",
                                      f"Interactive {name} ammonia and seawater temperature profiles with local temperature approach.")
            st.caption("Hover for both stream temperatures and local approach. Scroll to zoom, drag to pan, double-click to reset. Teal: ammonia; blue: seawater. The enthalpy coordinate is not exchanger length.")
    with cycle_tab:
        st.html(table(["State", "T · °C", "p · bar", "h · kJ/kg", "s · kJ/(kg K)", "Quality"],
                      [[f'{s["state"]} · {s["location"]}', n(s["temperature_c"], 2), n(s["pressure_bar"]), n(s["enthalpy_kj_kg"], 2),
                        n(s["entropy_kj_kgk"], 4), n(s["quality"], 4) if s["quality"] is not None else "Single phase"] for s in r["states"]]))
        left, right = st.columns(2)
        with left:
            st.markdown("#### Pressure–enthalpy state diagram")
            interactive_chart(cycle_state_diagram(r, "pressure_enthalpy"), "pressure_enthalpy",
                              "Interactive pressure–enthalpy diagram; hover over the four ammonia states for their thermodynamic properties.")
        with right:
            st.markdown("#### Temperature–entropy state diagram")
            interactive_chart(cycle_state_diagram(r, "temperature_entropy"), "temperature_entropy",
                              "Interactive temperature–entropy diagram; hover over the four ammonia states for their thermodynamic properties.")
        st.caption("Hover over a state for its properties. Scroll to zoom, drag to pan, double-click to reset. Straight connectors show state order schematically; intermediate process paths and saturation envelopes are not plotted. Absolute enthalpy/entropy depend on the property reference state.")
    with hydraulic_tab:
        flow_col, head_col = st.columns(2, gap="large")
        with flow_col:
            st.markdown("#### Seawater flow requirements")
            interactive_chart(hydraulic_flows(r), "seawater_flows", "Total warm and cold seawater flow requirements in cubic metres per second.")
        with head_col:
            st.markdown("#### Where pumping head is spent")
            interactive_chart(hydraulic_heads(r), "hydraulic_heads", "Warm and cold circuit head contributions from pipe friction, minor losses, heat exchangers and additional unrecovered head, in metres.")
        keys = [("Total flow · m³/s", "volume_m3_s", 3), ("Mass flow · kg/s", "mass_flow_kg_s", 1),
                ("Velocity per pipe · m/s", "velocity_m_s", 3), ("Reynolds number", "reynolds", 0),
                ("Darcy friction factor", "darcy_factor", 5), ("Friction head · m", "friction_head_m", 3),
                ("Minor-loss head · m", "minor_head_m", 3), ("HX head · m", "hx_head_m", 3),
                ("Total dynamic head · m", "total_head_m", 3)]
        rows = [[label, n(r["warm_hydraulics"][key], digits), n(r["cold_hydraulics"][key], digits)] for label, key, digits in keys]
        rows += [["Intake depth · m", n(d["warm_intake_depth_m"], 0), n(d["cold_intake_depth_m"], 0)],
                 ["Pipe length / branch · m", n(d["warm_length_m"], 0), n(d["cold_length_m"], 0)],
                 ["Parallel pipes", int(d["warm_pipes"]), int(d["cold_pipes"])],
                 ["Diameter / pipe · m", n(d["warm_diameter_m"], 2), n(d["cold_diameter_m"], 2)],
                 ["Additional unrecovered head · m", n(d["warm_static_head_m"]), n(d["cold_static_head_m"])],
                 ["Pump electricity · MW", n(r["warm_pump_mw"]), n(r["cold_pump_mw"])]]
        with st.expander("Detailed hydraulic values", key="hydraulic_values", on_change="rerun"):
            st.html(table(["Circuit metric", "Warm seawater", "Cold seawater"], rows))
        st.info("Intake depth is not pumping head. Flooded, hydrostatically balanced circuits are assumed. Include actual unbalanced elevation, density and discharge head explicitly.")
    with checks_tab:
        st.html(f'<div class="check-grid"><div class="check-card">Cycle first-law relative residual<strong>{r["first_law_relative_residual"]:.2e}</strong></div><div class="check-card">Electrical account relative residual<strong>{r["electrical_relative_residual"]:.2e}</strong></div><div class="check-card">Net cycle shaft efficiency<strong>{r["cycle_efficiency"]*100:.2f}%</strong></div><div class="check-card">Cycle / source Carnot upper bounds<strong>{r["cycle_carnot_efficiency"]*100:.2f}% / {r["source_carnot_efficiency"]*100:.2f}%</strong></div></div>')
        st.html(table(["Component entropy generation", "W/K"], [(key.replace("_w_k", "").capitalize(), n(value)) for key, value in r["entropy_generation"].items()]))
        st.caption("Checks: first-law/electrical residual ≤ 10⁻⁹; component entropy generation ≥ −10⁻⁵ W/K; applicable Carnot bound; user-specified minimum pinch and turbine quality. These checks verify the equations, not experimental accuracy.")
    with basis_tab:
        st.markdown("""This is a **steady-state design-sizing sandbox**. Saturated liquid leaves the condenser; saturated ammonia vapor enters the turbine. CoolProp supplies real-fluid properties. TEOS-10 supplies seawater properties at the plant heat exchangers (0 dbar sea pressure).

The ocean sandbox supports a single extracted profile at one time/location. It interpolates temperature and SP with depth and assumes reversible adiabatic transport to sea level. It does not download ocean data or read raw NetCDF files. The supplied profile is hypothetical.

Required exchanger area is calculated from local countercurrent LMTD and specified U/fouling. No fixed-area off-design model, ammonia-side pressure loss, pipe heat gain, pump curve, NPSH, marine structural loading, discharge plume, annual energy or economics is included. Exchanger U, seawater pressure drops, viscosity and efficiencies require equipment data. Turbine wetness limits require vendor confirmation.

**No IEC compliance or site-feasibility claim is made.** IEC TS 62600-20:2019 gives general OTEC design guidance; the full document has not been assessed clause by clause. Experimental and site-specific validation remain outstanding.

[CoolProp](https://coolprop.org/coolprop/HighLevelAPI.html) · [TEOS-10 / GSW](https://teos-10.github.io/GSW-Python/) · [IEC OTEC guidance](https://webstore.iec.ch/en/publication/28618)
""")

with st.expander("Model notes, provenance & downloads", expanded=True):
    for warning in r["warnings"]:
        st.caption("• " + warning)
    if r.get("ocean_resource"):
        src = r["ocean_resource"]
        st.caption(f'Applied profile: {src.get("source_label", "User scenario")} · {src["temperature_type"]} · warm {src["warm_depth_m"]:g} m / cold {src["cold_depth_m"]:g} m. Resource snapshot included in JSON.')
    left, right, _ = st.columns([1, 1, 1.5])
    disabled = bool(st.session_state.error)
    with left:
        st.download_button("Full result · JSON", json.dumps(r, indent=2, allow_nan=False), f'otec-{d["capacity_mw"]:g}mw.json', "application/json", disabled=disabled, width="stretch", on_click="ignore")
    with right:
        st.download_button("Engineering tables · CSV", engineering_csv(r), f'otec-{d["capacity_mw"]:g}mw.csv', "text/csv", disabled=disabled, width="stretch", on_click="ignore")
    st.caption(f'Model {r["model_version"]} · CoolProp {r["libraries"]["CoolProp"]} · GSW {r["libraries"]["gsw"]} · Calculated {r["timestamp_utc"]}')

st.html('<div class="status-line">Design-point research tool · Numerical verification completed; experimental validation outstanding · No cloud computation or external data requests</div>')
