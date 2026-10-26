"""Small, dependency-free HTML/SVG views for the scientific workspace."""
from html import escape


def number(value, digits=3):
    return f"{value:,.{digits}f}"


def table(headers, rows):
    return ('<div class="table-scroll"><table class="engineering-table"><thead><tr>'
            + ''.join(f'<th>{escape(str(v))}</th>' for v in headers)
            + '</tr></thead><tbody>'
            + ''.join('<tr>' + ''.join(f'<td>{escape(str(v))}</td>' for v in row) + '</tr>' for row in rows)
            + '</tbody></table></div>')


def line_chart(series, xlabel, ylabel, depth=False):
    """series = [(color, [(x,y),...], point-labels or None)]."""
    w, h, left, right, top, bottom = 560, 310, 66, 30, 29, 49
    points = [p for _, data, _ in series for p in data]
    xmin, xmax = min(x for x, _ in points), max(x for x, _ in points)
    ymin, ymax = min(y for _, y in points), max(y for _, y in points)
    xp, yp = (xmax - xmin) * .12 or 1, (ymax - ymin) * .12 or 1
    xmin, xmax, ymin, ymax = xmin - xp, xmax + xp, ymin - yp, ymax + yp
    if depth:
        ymin = 0
    def sx(x):
        return left + (x - xmin) / (xmax - xmin) * (w - left - right)
    def sy(y):
        f = (y - ymin) / (ymax - ymin)
        return top + f * (h - top - bottom) if depth else h - bottom - f * (h - top - bottom)
    out = [f'<svg viewBox="0 0 {w} {h}" style="display:block;width:100%;height:auto" role="img" '
           f'aria-label="{escape(ylabel)} versus {escape(xlabel)}" xmlns="http://www.w3.org/2000/svg">']
    for i in range(5):
        x, y = xmin + (xmax - xmin) * i / 4, ymin + (ymax - ymin) * i / 4
        out += [f'<path d="M{left},{sy(y):.2f}H{w-right}" stroke="#dce8ee"/>',
                f'<text x="{left-10}" y="{sy(y)+4:.2f}" text-anchor="end" font-size="11" fill="#6b8290">{y:,.0f}</text>',
                f'<text x="{sx(x):.2f}" y="{h-bottom+21}" text-anchor="middle" font-size="11" fill="#6b8290">{x:.1f}</text>']
    for color, data, labels in series:
        out.append(f'<polyline points="' + ' '.join(f'{sx(x):.3f},{sy(y):.3f}' for x, y in data)
                   + f'" fill="none" stroke="{color}" stroke-width="2.5"/>')
        if labels:
            for (x, y), label in zip(data, labels):
                out += [f'<circle cx="{sx(x):.3f}" cy="{sy(y):.3f}" r="4.5" fill="{color}" stroke="white" stroke-width="1.5"/>',
                        f'<text x="{sx(x)+8:.3f}" y="{sy(y)-8:.3f}" font-size="11" fill="{color}">{escape(str(label))}</text>']
    out += [f'<text x="{(left+w-right)/2}" y="{h-5}" text-anchor="middle" font-size="12" fill="#6b8290">{escape(xlabel)}</text>',
            f'<text transform="translate(16 {(top+h-bottom)/2}) rotate(-90)" text-anchor="middle" font-size="12" fill="#6b8290">{escape(ylabel)}</text>', '</svg>']
    return ''.join(out)


def ocean_chart(resource):
    series = [("#55849b", [(r["temperature_c"], r["depth_m"]) for r in resource["profile"]], None)]
    for side, color in (("warm", "#c28148"), ("cold", "#2c83ab")):
        r = resource[side]
        series.append((color, [(r["profile_temperature_c"], r["depth_m"])], [side.capitalize()]))
    return line_chart(series, "Profile temperature · °C", "Depth below sea surface · m", depth=True)


def power_bars(result):
    data = [("Gross generator", result["gross_mw"], "#477fa5"),
            ("Warm seawater pump", result["warm_pump_mw"], "#7ba5b4"),
            ("Cold seawater pump", result["cold_pump_mw"], "#52889f"),
            ("Working-fluid pump", result["wf_pump_mw"], "#8c99b4"),
            ("Other auxiliaries", result["auxiliary_mw"], "#bd9e71"),
            ("Net electrical output", result["net_mw"], "#087f80")]
    return ''.join(f'<div class="power-row"><span>{label}</span><div class="power-track"><div style="width:{max(0,min(100,v/result["gross_mw"]*100)):.2f}%;background:{color}"></div></div><b>{v:,.3f} <small>MW</small></b></div>' for label, v, color in data)


def metrics(result):
    data = [("NET ELECTRICAL OUTPUT", result["net_mw"], "MW", "After all modeled parasitic loads", 3),
            ("GROSS GENERATOR OUTPUT", result["gross_mw"], "MW", "Required before pumps & auxiliaries", 3),
            ("EVAPORATOR HEAT INPUT", result["heat_input_mw"], "MWₜₕ", "Required thermal duty", 2),
            ("NET THERMAL EFFICIENCY", result["net_efficiency"] * 100, "%", "Net electrical / evaporator heat", 2)]
    return '<div class="metric-grid">' + ''.join(f'<div class="metric-card {"featured" if i == 0 else ""}"><div class="metric-label">{label}</div><div class="metric-value">{number(v,digits)} <span>{unit}</span></div><div class="metric-note">{note}</div></div>' for i, (label, v, unit, note, digits) in enumerate(data)) + '</div>'
