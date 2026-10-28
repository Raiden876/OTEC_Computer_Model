# OTEC engineering sandbox

A local Python application for sizing a **closed-cycle ammonia (R717) OTEC plant** at a user-entered electrical target from **10 kW to 100 MW**. Enter the target in either kW or MW. The main interface is a professionally styled **Streamlit engineering workspace**, with an ocean-depth sandbox, reference-size comparisons, thermodynamic diagrams, hydraulic calculations, and exports. An alternative standard-library browser GUI is included for a smaller installation.

The existing `.venv` is used. The scientific engine needs only CoolProp, GSW, and NumPy; the main interface adds Streamlit and its dependencies. There is no database, cloud computation, additional plotting library, CDN, or automatic ocean-data download. Interactive charts use Streamlit's bundled Vega-Lite renderer. After installation, operation needs no internet connection. Calculations use SI internally, with display units shown beside inputs and outputs. Streamlit telemetry and filesystem watching are disabled in the local configuration.

## Run

From this directory:

```bash
.venv/bin/python run.py
```

Open **http://127.0.0.1:8501** if the browser does not open automatically. Keep the terminal open; press **Ctrl+C** to stop. The server binds only to your own computer. `run.py` always uses this project's `.venv`, even when invoked with another Python interpreter.

The equivalent direct Streamlit command is:

```bash
.venv/bin/python -m streamlit run streamlit_app.py
```

Alternative lightweight GUI and scientific CLI:

```bash
# Standard-library GUI; no Streamlit required
.venv/bin/python run.py --light

# Start the lightweight GUI with a 500 kW net design
.venv/bin/python app.py --capacity 500 --unit kW

# Choose a different port and open the URL yourself
.venv/bin/python app.py --no-browser --port 8502

# Calculate a 2.5 MW design without starting the GUI
.venv/bin/python app.py --check --capacity 2.5 --unit MW > result-2_5mw.json

# Explicit alternative capacity basis
.venv/bin/python app.py --check --capacity 1 --basis gross
```

If recreating the environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-streamlit.txt
```

For only the smaller GUI and solver, install `requirements.txt` instead. Python 3.12 is used in this workspace. Requirements pin the scientific libraries and Streamlit version actually installed. `python run.py` also works after activating `.venv` with `source .venv/bin/activate`.

## Use the GUI

1. Enter any target from **10 kW to 100 MW** and choose **kW** or **MW**; the default is **1 MW**. Capacity defaults to **net electricity after all modeled pumps and auxiliaries**. The internal model and exported scenarios store capacity in MW.
2. Use the single **Ocean source & intake settings** panel for profile levels, both intake depths, temperature definition, and coordinates. Other input groups cover cycle temperatures, seawater temperature changes, efficiencies, pipe geometry, and heat-transfer assumptions.
3. Edit the sidebar inputs, then select **Apply edits & calculate**. The draft heat-exchanger temperatures and salinities update as you finish each field. The sidebar also shows the last applied values, then confirms successful application or explains why an infeasible edit was rejected. If a design is infeasible, the previous valid result remains clearly identified and result exports are disabled until a new design solves.
4. Use **Compare reference sizes** for a side-by-side comparison of the 1, 10, 25, and 50 MW reference cases at your source conditions.
5. Review thermodynamic states, hydraulic losses, heat-transfer profiles, and numerical verification tabs. Download full results as JSON or engineering tables as CSV. Save/load input JSON to reproduce a case.

Changing the target applies the illustrative pipe count and diameter scaling rule; other applied inputs are retained. **Reset to 1 MW reference** restores the Streamlit reference design. The lightweight GUI resets to its initial command-line target. The included `designs/` files can be loaded through the scenario/input import controls.

In the Streamlit GUI, there is one ocean source workflow: an editable depth profile in the sidebar. Profile levels, intake depths, coordinates, and temperature definition all feed the same live graph and derive the plant inlet temperatures and salinities. They live together in the **Ocean source & intake settings** panel. The **01 / Ocean resource** panel previews that same draft. Select **Apply edits & calculate** once to apply it to the model. The initial profile is hypothetical, interpolation does not extrapolate beyond its levels, and profile depths must include both intakes. The **Keep existing inlet-to-cycle temperature margins** option moves the saturation temperatures with the new source conditions; it still re-sizes the plant at the requested net target. It does not predict off-design output of installed equipment.

The selected profile and sampled intake temperatures remain visible above the performance results. The interactive chart redraws from the current sidebar draft, marks both intake depths, shows values on hover, and supports zooming. Edit the profile table in the sidebar to add, remove, or change levels; edits remain a draft until **Apply edits & calculate** is selected.

Plant performance includes the required ammonia circulation rate in kg/s for the applied electrical design and in the reference-size comparison. This is the mass circulating through the closed loop each second, not the total ammonia charge; the model does not calculate inventory. Plant performance is also presented as a gross-to-net electrical waterfall, required exchanger area/duty charts, seawater flow bars and a stacked hydraulic-head breakdown. Reference-size comparison charts show electricity, seawater demand and exchanger area on separate tabs with consistent units. Exact engineering tables remain available in expandable sections. These views use the solver results directly and do not change the physical model.

The evaporator and condenser temperature profiles and both cycle state diagrams support hover tooltips, wheel zoom, drag to pan, and double-click to reset. Profile tooltips show both stream temperatures and the local approach; state tooltips show location, temperature, pressure, enthalpy, entropy, and vapor quality. These diagrams use the applied design and refresh after **Apply edits & calculate**.

For in-situ profile inputs, GSW calculates pressure from depth and latitude, converts Practical to Absolute Salinity at the intake, and computes potential temperature with `pt0_from_t` for reversible adiabatic ascent to sea level. For `thetao`-like potential-temperature inputs, that temperature is already referenced to sea level; GSW separately reconstructs intake in-situ temperature for reporting. Absolute Salinity is conserved using an equivalent plant-pressure SP in the core solver. No intake heat gain or mixing is modeled. A separate pipe-reach constraint rejects paths shorter than intake depth. Resource snapshots are included in JSON exports and saved scenarios when the applied design uses a profile.

## What the sizing cases mean

These are **separately sized design points**, not four certified equipment designs or predictions for installed plants. The working-fluid circulation is solved to deliver the requested net output, accounting for flow-dependent seawater pumping. Net capacity cannot always be achieved with a given pipe geometry; the solver searches the increasing-output branch and rejects an unattainable target.

Illustrative pipe geometry uses `n = ceil(capacity_MW / 5)` equal parallel branches in each seawater circuit, with internal diameter `D = 2.4 sqrt(capacity_MW / n)` metres. The same rule applies when a user enters a custom target. It is an explicit illustrative scaling rule, not an optimization or a construction recommendation. Parallel branches all have the specified pipe length and share total circuit flow equally.

Reference calculation with 28 °C / 5 °C seawater, 22 °C evaporation, 10 °C condensation, SP=35, and the other supplied defaults:

| Target net MW | Required gross MW | Warm flow m³/s | Cold flow m³/s | Evaporator area m² | Condenser area m² | Pipes per circuit |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1.211 | 3.01 | 2.91 | 2,985 | 3,964 | 1 × 2.40 m |
| 10 | 12.066 | 30.01 | 28.95 | 29,746 | 39,502 | 2 × 5.37 m |
| 25 | 30.165 | 75.02 | 72.39 | 74,364 | 98,755 | 5 × 5.37 m |
| 50 | 60.329 | 150.05 | 144.77 | 148,729 | 197,510 | 10 × 5.37 m |

Values are rounded, hypothetical sizing results. Large seawater flows and exchanger areas are substantive engineering requirements, not evidence that these layouts are buildable. Differences in net-to-gross ratio between reference cases reflect the explicit pipe geometry rule. No constant thermal efficiency is prescribed.

## Physics and control-volume definitions

### Ammonia loop

CoolProp's HEOS ammonia equation of state provides pressure, enthalpy, entropy, temperature and vapor quality. State 1 is saturated liquid at condenser pressure; state 3 is saturated vapor at evaporator pressure. No superheat, subcooling, or working-fluid heat-exchanger pressure loss is included.

```text
h2s = h(p_evap, s1)             h2 = h1 + (h2s − h1) / η_wf_pump
h4s = h(p_cond, s3)             h4 = h3 − η_turbine (h3 − h4s)
Q_evap = m_wf (h3 − h2)         Q_cond = m_wf (h4 − h1)
W_turbine = m_wf (h3 − h4)      W_wf_pump,shaft = m_wf (h2 − h1)
P_gross = η_generator W_turbine
P_wf_pump,electric = W_wf_pump,shaft / η_wf_motor
P_aux = auxiliary_fraction P_gross + fixed_auxiliary_kW × 1000
P_net = P_gross − P_wf_pump,electric − P_warm_pump − P_cold_pump − P_aux
```

The cycle first law is `Q_evap − Q_cond = W_turbine − W_wf_pump,shaft`. Electrical losses are accounted separately, so pump work is never subtracted twice. Cycle thermal efficiency uses net **shaft** work; net plant efficiency uses **net electrical** power. Electrical and shaft efficiencies must not be conflated.

All Carnot limits use kelvin. Both the seawater inlet temperature bound and the narrower evaporation/condensation bound are displayed. Negative net output is retained for a gross-rated case; it is never silently clipped to zero.

### Seawater

GSW implements TEOS-10. Practical Salinity is converted with `SA_from_SP` at the supplied geographic location. Density, exact enthalpy and entropy are evaluated at **0 dbar sea pressure at the plant heat exchangers**. Mean-temperature density is used for each hydraulic circuit.

```text
m_warm = Q_evap / [h_sw(T_warm,in) − h_sw(T_warm,out)]
m_cold = Q_cond / [h_sw(T_cold,out) − h_sw(T_cold,in)]
```

Temperature inputs mean **water arriving at the heat exchangers**, not raw deep-ocean temperature. GLORYS `thetao` is potential temperature; it cannot be used blindly as deep in-situ temperature. The core solver accepts plant inlet temperatures directly; the Streamlit interface derives them from its single ocean profile and applies the adiabatic conversion described above. This version does not read raw NetCDF reanalysis, calculate intake heat gain, or integrate stratified hydrostatic columns. Geographic coordinates affect salinity and depth-pressure conversions; they do not fetch or validate a site.

### Countercurrent exchanger sizing

For specified saturation temperatures and seawater outlet changes, the solver determines thermal duty and the required surface area. It samples 48 working-fluid enthalpy segments, explicitly inserting saturated liquid/vapor boundaries. At each coordinate, exact seawater enthalpy is inverted to temperature with bounded bisection.

```text
U_effective = 1 / (1 / U_clean + R_fouling)
A_required = Σ [ΔQ_segment / (U_effective × LMTD_segment)]
LMTD = (ΔT_a − ΔT_b) / ln(ΔT_a / ΔT_b)
```

Equal terminal temperature differences use the continuous LMTD limit. Every sampled local approach must exceed the user-specified minimum. The model resolves sensible preheating and phase change, rather than representing the whole evaporator as an isothermal boundary. The discrete mesh is a numerical approximation; a convergence test compares 24 and 96 segments.

**These are required areas, not constraints from fixed installed exchangers.** Specified U and pressure losses are independent inputs. Their physical compatibility must eventually be checked with actual exchanger geometry, channel velocities, correlations and vendor measurements. Fouling increases required area in this sizing mode; it does not simulate degradation of a fixed installed plant.

### Hydraulic circuits

```text
Q_volume = m_seawater / ρ
v = Q_volume / (n π D² / 4)
Re = ρ v D / μ
H_friction = f_D (L / D) v² / (2g)
H_minor = ΣK v² / (2g)
H_HX = Δp_HX / (ρg)
H_total = H_friction + H_minor + H_HX + H_additional
P_pump,electric = ρg Q_volume H_total / (η_hydraulic η_motor)
```

`f_D` is the **Darcy** factor: `64/Re` for Re ≤ 2300; implicit Colebrook–White iteration for Re ≥ 4000; linear interpolation between endpoint factors in the transitional region, which generates a warning. Dynamic viscosity is a visible user assumption, not a TEOS-10 output. `g = 9.80665 m/s²`.

`L` is total friction path per branch. **A 1,000 m intake depth does not imply 1,000 m of pump head.** The reference arrangement assumes a flooded, hydrostatically balanced seawater path. Actual additional elevation, stratification, density and discharge effects must be established from a complete layout and entered as additional unrecovered head. Negative additional heads/energy recovery are not modeled.

## Verification and engineering maturity

Run the scientific tests:

```bash
.venv/bin/python -m unittest test_otec test_ocean -v
```

Run scientific, Streamlit interaction, and HTTP integration tests (requires permission to bind a temporary local port):

```bash
.venv/bin/python -m unittest discover -v
```

The tests cover the four reference targets; distinct gross/net accounting; energy conservation including conversion losses; Carnot bounds; component entropy generation; the ideal isentropic limit; comparison to a preliminary seawater heat-capacity estimate; exchanger mesh convergence; hydraulic sensitivity; Colebrook residuals; salinity dependence; and rejection of freezing, wetness, pinch, non-finite and infeasible inputs. Ocean tests additionally verify depth interpolation, potential/in-situ temperature definitions, adiabatic entropy conservation, Absolute Salinity conservation, no extrapolation, and pipe reach. HTTP checks cover GUI delivery, calculation requests, and the depth-resource API. Streamlit AppTest checks sidebar edits and calculation, depth-to-plant application, comparison, invalid-design handling, disabled downloads, invalid resource selection and reset. The optional Streamlit tests are skipped when only core requirements are installed.

Accepted runs enforce a cycle first-law residual ≤ 10⁻⁹ relative to heat input, component entropy generation ≥ −10⁻⁵ W/K (numerical tolerance), cycle efficiency below its applicable Carnot bound, the specified pinch, and the specified turbine quality. Pump and generator losses have distinct fields in the export. JSON includes every input, model version, property-library versions and calculation time in UTC.

**Verification is not experimental validation.** No measured OTEC dataset or vendor performance map has been supplied. The application therefore makes no claim of validated absolute accuracy, IEC compliance, plant safety, or site feasibility.

[IEC TS 62600-20:2019](https://webstore.iec.ch/en/publication/28618) is relevant general guidance for OTEC design and marine intake systems. Only its public scope has been consulted; the full specification has not been assessed clause by clause. This prototype supports explicit assumptions, physically defined interfaces, operating constraints and numerical checks. Compliance assessment still requires the applicable standards and qualified engineering review.

Important omitted engineering work includes ammonia containment and materials compatibility; turbine moisture tolerances; pump curves and NPSH/cavitation; ammonia piping pressure losses; noncondensables; controls and startup; fixed-geometry off-design operation; thermal/hydraulic coupling from detailed exchanger geometry; pipe structural/buckling/fatigue assessment; waves and currents; environmental discharge modeling; measured ocean data; annual energy; uncertainty propagation; and economic feasibility. No annual production or LCOE is inferred from a single design point.

## References and implementation

- [CoolProp high-level property API](https://coolprop.org/coolprop/HighLevelAPI.html): SI property calls, phase definitions, enthalpy and entropy input pairs.
- [CoolProp ammonia property model](https://coolprop.org/fluid_properties/fluids/Ammonia.html): underlying fluid-model references and limits.
- [GSW-Python / TEOS-10](https://teos-10.github.io/GSW-Python/): authoritative seawater property implementation.
- [GSW function definitions and units](https://teos-10.github.io/GSW-Python/gsw_flat.html): `SA_from_SP`, `rho`, `enthalpy_t_exact`, `entropy_from_t` and freezing temperature.
- [IEC TS 62600-20:2019 public scope](https://webstore.iec.ch/en/publication/28618): OTEC design/assessment guidance.

`run.py` launches the main Streamlit interface in `streamlit_app.py`; `streamlit_theme.css` and `views.py` provide local styling, tables, and metrics. `charts.py` defines the interactive charts and diagrams. `ocean.py` handles depth interpolation and TEOS-10 intake conversion. `otec.py` is the independent scientific solver. `app.py` and `ui.html` supply the alternative standard-library HTTP/CLI interface. Tests live in `test_otec.py`, `test_ocean.py`, `test_app.py`, and `test_streamlit.py`. No dependency is downloaded when the application runs.
