"""Interactive Vega-Lite specifications using Streamlit's bundled chart renderer.

No new plotting dependency or calculation is introduced here. Values come from
the verified resource and plant solvers; physical units remain separate.
"""
WARM = "#c28148"
COLD = "#377fa5"
TEAL = "#087f80"


def chart(spec, height=280):
    return {"height": height, "config": {
        "view": {"stroke": None},
        "axis": {"labelFontSize": 11, "titleFontSize": 11, "labelColor": "#587787",
                 "titleColor": "#587787", "gridColor": "#e3edf2", "domain": False,
                 "labelPadding": 7, "titlePadding": 12},
        "legend": {"orient": "bottom", "title": None, "labelColor": "#587787", "labelFontSize": 11},
    }, **spec}


def ocean_profile(resource):
    points = [{"depth": p["depth_m"], "temperature": p["temperature_c"], "salinity": p["salinity_sp"]}
              for p in resource["profile"]]
    intakes = []
    for side in ("warm", "cold"):
        temperature = resource[side].get("profile_temperature_c")
        if temperature is None:
            continue
        intake = {"depth": resource[side]["depth_m"],
                  "temperature": temperature,
                  "intake": side.capitalize() + " intake"}
        if "plant_inlet_c" in resource[side]:
            intake["plant_temperature"] = resource[side]["plant_inlet_c"]
        intakes.append(intake)
    x = {"field": "temperature", "type": "quantitative", "title": "Profile temperature (°C)", "scale": {"zero": False, "nice": True}}
    y = {"field": "depth", "type": "quantitative", "title": "Depth below sea surface (m)",
         "scale": {"reverse": True, "zero": True}}
    colors = {"field": "intake", "type": "nominal", "scale": {"domain": ["Warm intake", "Cold intake"], "range": [WARM, COLD]}}
    intake_tooltip = [{"field": "intake", "title": "Selected intake"},
                      {"field": "depth", "type": "quantitative", "title": "Depth (m)", "format": ",.0f"},
                      {"field": "temperature", "type": "quantitative", "title": "Profile temperature (°C)", "format": ".2f"}]
    if any("plant_temperature" in intake for intake in intakes):
        intake_tooltip.append({"field": "plant_temperature", "type": "quantitative",
                               "title": "At plant after ascent (°C)", "format": ".2f"})
    layers = [
        {"data": {"values": points},
         "params": [{"name": "ocean_zoom", "select": {"type": "interval", "clear": "dblclick"}, "bind": "scales"}],
         "mark": {"type": "line", "color": "#54849b", "strokeWidth": 3, "point": {"filled": True, "size": 42}},
         "encoding": {"x": x, "y": y, "order": {"field": "depth", "type": "quantitative"},
                      "tooltip": [{"field": "depth", "type": "quantitative", "title": "Depth (m)", "format": ",.0f"},
                                  {"field": "temperature", "type": "quantitative", "title": "Profile temperature (°C)", "format": ".2f"},
                                  {"field": "salinity", "type": "quantitative", "title": "Practical Salinity", "format": ".3f"}]}}
    ]
    if intakes:
        layers.extend([
            {"data": {"values": intakes}, "mark": {"type": "rule", "strokeDash": [5, 4], "opacity": .55},
             "encoding": {"y": y, "color": colors}},
            {"data": {"values": intakes}, "mark": {"type": "point", "filled": True, "size": 170, "stroke": "white", "strokeWidth": 2},
             "encoding": {"x": x, "y": y, "color": colors,
                          "tooltip": intake_tooltip}}
        ])
    return chart({"layer": layers}, height=330)


def electrical_waterfall(result):
    gross = result["gross_mw"]
    rows = [{"stage": "Gross", "start": 0, "end": gross, "change": gross, "role": "Generator"}]
    balance = gross
    for label, key in (("Warm pump", "warm_pump_mw"), ("Cold pump", "cold_pump_mw"),
                       ("NH₃ pump", "wf_pump_mw"), ("Auxiliaries", "auxiliary_mw")):
        power = result[key]
        rows.append({"stage": label, "start": balance, "end": balance - power, "change": -power, "role": "Parasitic load"})
        balance -= power
    rows.append({"stage": "Net", "start": 0, "end": result["net_mw"], "change": result["net_mw"], "role": "Net output"})
    for row in rows:
        row["top"] = max(row["start"], row["end"])
    encoding = {"x": {"field": "stage", "type": "ordinal", "sort": [r["stage"] for r in rows], "title": None,
                      "axis": {"labelAngle": -20, "labelLimit": 100}},
                "tooltip": [{"field": "stage", "title": "Electrical account"},
                            {"field": "change", "type": "quantitative", "title": "Power / change (MW)", "format": ".3f"},
                            {"field": "end", "type": "quantitative", "title": "Balance after step (MW)", "format": ".3f"}]}
    return chart({"data": {"values": rows}, "encoding": encoding, "layer": [
        {"mark": {"type": "bar", "size": 36, "cornerRadius": 3}, "encoding": {
            "y": {"field": "start", "type": "quantitative", "title": "Electrical power (MW)", "scale": {"zero": True}},
            "y2": {"field": "end"}, "color": {"field": "role", "type": "nominal",
                "scale": {"domain": ["Generator", "Parasitic load", "Net output"], "range": [COLD, WARM, TEAL]}}}},
        {"mark": {"type": "text", "dy": -10, "fontSize": 10, "color": "#35596b"}, "encoding": {
            "y": {"field": "top", "type": "quantitative"}, "text": {"field": "change", "type": "quantitative", "format": ".3f"}}}
    ]}, height=270)


def grouped_bars(rows, unit, groups, colors, height=240):
    """Rows: category, series, value. All values share one physical unit."""
    categories = list(dict.fromkeys(row["category"] for row in rows))
    return chart({"data": {"values": rows}, "mark": {"type": "bar", "cornerRadiusEnd": 3},
                  "encoding": {"x": {"field": "category", "type": "nominal", "title": None, "sort": categories,
                                     "axis": {"labelAngle": 0}},
                               "xOffset": {"field": "series", "sort": groups},
                               "y": {"field": "value", "type": "quantitative", "title": unit, "scale": {"zero": True}},
                               "color": {"field": "series", "type": "nominal", "scale": {"domain": groups, "range": colors}},
                               "tooltip": [{"field": "category", "title": "Design / equipment"},
                                           {"field": "series", "title": "Quantity"},
                                           {"field": "value", "type": "quantitative", "title": unit, "format": ",.3f"}]}}, height)


def exchanger_sizing(result, metric):
    key, unit, factor = (("area_m2", "Required heat-transfer area (m²)", 1) if metric == "area"
                         else ("duty_w", "Thermal duty (MWₜₕ)", 1e-6))
    rows = [{"category": name.capitalize(), "series": "Required area" if metric == "area" else "Thermal duty", "value": result[name][key] * factor}
            for name in ("evaporator", "condenser")]
    return grouped_bars(rows, unit, [rows[0]["series"]], [TEAL])


def heat_transfer_profile(result, exchanger):
    rows = []
    for point in result[exchanger]["profile"]:
        values = {"coordinate_pct": point["fraction"] * 100,
                  "ammonia_c": point["working_fluid_c"],
                  "seawater_c": point["seawater_c"], "approach_k": point["approach_k"]}
        for stream, temperature in (("Ammonia", point["working_fluid_c"]),
                                    ("Seawater", point["seawater_c"])):
            rows.append({**values, "stream": stream, "temperature_c": temperature})
    return chart({
        "data": {"values": rows},
        "params": [{"name": exchanger + "_zoom", "select": {"type": "interval", "clear": "dblclick"},
                    "bind": "scales"}],
        "mark": {"type": "line", "strokeWidth": 2.5, "clip": True,
                 "point": {"filled": True, "size": 24}},
        "encoding": {
            "x": {"field": "coordinate_pct", "type": "quantitative",
                  "title": "Working-fluid enthalpy coordinate (%)", "scale": {"domain": [0, 100]}},
            "y": {"field": "temperature_c", "type": "quantitative", "title": "Temperature (°C)",
                  "scale": {"zero": False}},
            "color": {"field": "stream", "type": "nominal",
                      "scale": {"domain": ["Ammonia", "Seawater"], "range": [TEAL, COLD]}},
            "order": {"field": "coordinate_pct", "type": "quantitative"},
            "tooltip": [{"field": "stream", "title": "Stream"},
                        {"field": "coordinate_pct", "type": "quantitative", "title": "Coordinate (%)", "format": ".1f"},
                        {"field": "ammonia_c", "type": "quantitative", "title": "Ammonia (°C)", "format": ".2f"},
                        {"field": "seawater_c", "type": "quantitative", "title": "Seawater (°C)", "format": ".2f"},
                        {"field": "approach_k", "type": "quantitative", "title": "Local approach (K)", "format": ".2f"}],
        },
    })


def cycle_state_diagram(result, kind):
    x_field, x_title, y_field, y_title, color = {
        "pressure_enthalpy": ("enthalpy_kj_kg", "Enthalpy (kJ/kg)", "pressure_bar", "Absolute pressure (bar)", TEAL),
        "temperature_entropy": ("entropy_kj_kgk", "Entropy (kJ/(kg K))", "temperature_c", "Temperature (°C)", COLD),
    }[kind]
    states = [{**state, "quality_label": "Single phase" if state["quality"] is None else f'{state["quality"]:.4f}'}
              for state in result["states"]]
    path = [{**state, "step": step} for step, state in enumerate(states + [states[0]])]
    x = {"field": x_field, "type": "quantitative", "title": x_title, "scale": {"zero": False, "padding": 20}}
    y = {"field": y_field, "type": "quantitative", "title": y_title, "scale": {"zero": False, "padding": 20}}
    tooltip = [{"field": "state", "title": "State"}, {"field": "location", "title": "Location"},
               {"field": "temperature_c", "type": "quantitative", "title": "Temperature (°C)", "format": ".2f"},
               {"field": "pressure_bar", "type": "quantitative", "title": "Absolute pressure (bar)", "format": ".3f"},
               {"field": "enthalpy_kj_kg", "type": "quantitative", "title": "Enthalpy (kJ/kg)", "format": ".2f"},
               {"field": "entropy_kj_kgk", "type": "quantitative", "title": "Entropy (kJ/(kg K))", "format": ".4f"},
               {"field": "quality_label", "title": "Vapor quality"}]
    # Bind zoom on one layer; all three layers use the same scales.
    return chart({
        "layer": [
            {"data": {"values": path},
             "params": [{"name": kind + "_zoom", "select": {"type": "interval", "clear": "dblclick"}, "bind": "scales"}],
             "mark": {"type": "line", "color": color, "strokeWidth": 2.5, "clip": True},
             "encoding": {"x": x, "y": y, "order": {"field": "step", "type": "quantitative"}}},
            {"data": {"values": states},
             "mark": {"type": "point", "filled": True, "color": color, "size": 110,
                      "stroke": "white", "strokeWidth": 1.5, "clip": True},
             "encoding": {"x": x, "y": y, "tooltip": tooltip}},
            {"data": {"values": states},
             "mark": {"type": "text", "color": color, "dy": -13, "fontSize": 12, "clip": True},
             "encoding": {"x": x, "y": y, "text": {"field": "state"}}},
        ],
    })


def hydraulic_flows(result):
    return grouped_bars([{"category": "Seawater circuit", "series": side.capitalize(), "value": result[side + "_hydraulics"]["volume_m3_s"]}
                         for side in ("warm", "cold")], "Total flow (m³/s)", ["Warm", "Cold"], [WARM, COLD])


def hydraulic_heads(result):
    rows = []
    parts = [("Pipe friction", "friction_head_m"), ("Minor losses", "minor_head_m"), ("Heat exchanger", "hx_head_m")]
    for side in ("warm", "cold"):
        for order, (label, key) in enumerate(parts):
            rows.append({"circuit": side.capitalize(), "component": label, "head": result[side + "_hydraulics"][key], "order": order})
        rows.append({"circuit": side.capitalize(), "component": "Additional head", "head": result["inputs"][side + "_static_head_m"], "order": 3})
    return chart({"data": {"values": rows}, "mark": {"type": "bar", "size": 52}, "encoding": {
        "x": {"field": "circuit", "type": "nominal", "title": None, "sort": ["Warm", "Cold"], "axis": {"labelAngle": 0}},
        "y": {"field": "head", "type": "quantitative", "title": "Required dynamic head (m)", "stack": "zero"},
        "color": {"field": "component", "type": "nominal", "scale": {"domain": [label for label, _ in parts] + ["Additional head"],
                  "range": ["#477fa5", "#9cb8c8", "#087f80", WARM]}},
        "order": {"field": "order", "type": "quantitative"},
        "tooltip": [{"field": "circuit", "title": "Circuit"}, {"field": "component", "title": "Head contribution"},
                    {"field": "head", "type": "quantitative", "title": "Head (m)", "format": ".3f"}]}}, height=240)


def capacity_comparison(cases, metric):
    rows = []
    if metric == "power":
        groups, colors, unit = ["Gross", "Net"], [COLD, TEAL], "Electrical power (MW)"
    elif metric == "flow":
        groups, colors, unit = ["Warm", "Cold"], [WARM, COLD], "Total seawater flow (m³/s)"
    else:
        groups, colors, unit = ["Evaporator", "Condenser"], [TEAL, COLD], "Required exchanger area (m²)"
    for case in cases:
        if "result" not in case:
            continue
        r = case["result"]
        values = ([r["gross_mw"], r["net_mw"]] if metric == "power" else
                  [r["warm_hydraulics"]["volume_m3_s"], r["cold_hydraulics"]["volume_m3_s"]] if metric == "flow" else
                  [r["evaporator"]["area_m2"], r["condenser"]["area_m2"]])
        for group, value in zip(groups, values):
            rows.append({"category": f'{case["capacity"]} MW target', "series": group, "value": value})
    return grouped_bars(rows, unit, groups, colors, height=260)
