"""Omaha metro police activity dashboard.

Covers Omaha PD, Council Bluffs PD and the Sarpy County agencies (Bellevue,
Papillion, La Vista, Sheriff). Ralston PD publishes no machine-readable feed and
is absent. OPD publishes NIBRS offence records with no stop or disposition data,
so it contributes nothing to the enforcement panels.
"""

from dash import Dash, Input, Output, callback, dash_table, dcc, html
import plotly.express as px
import plotly.graph_objects as go

import analysis

conn = analysis.connect()
INCIDENTS = analysis.load_incidents(conn)
CAMERAS = analysis.load_cameras(conn)
AGENCIES = analysis.agency_options(conn)
CATEGORIES = analysis.category_options(conn)
DATE_LO, DATE_HI = analysis.date_bounds(conn)
conn.close()

DEFAULT_AGENCIES = [a for a in analysis.POLICE_AGENCIES if a in AGENCIES]
CENTER = {"lat": 41.21, "lon": -95.97}
MAP_SAMPLE = 15000
# Plotly writes colours into the figure, so the scheme has to be known before a
# figure is built. assets/theme.js reports the media query into the theme store
# and every figure callback reads it; nothing is restyled after the fact.
PALETTES = {
    "light": {"fg": "#111", "grid": "#e6e6e6", "legend": "rgba(255,255,255,.85)",
              "basemap": "open-street-map"},
    "dark": {"fg": "#e8e8ea", "grid": "#333840", "legend": "rgba(27,30,36,.85)",
             "basemap": "carto-darkmatter"},
}


def themed(fig, theme):
    p = PALETTES.get(theme, PALETTES["light"])
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font_color=p["fg"], legend_bgcolor=p["legend"],
                      legend_font_color=p["fg"])
    # update_xaxes would bolt empty axis objects onto the map figure, which has
    # no cartesian axes at all.
    if not any(tr.type == "scattermap" for tr in fig.data):
        fig.update_xaxes(gridcolor=p["grid"], zerolinecolor=p["grid"])
        fig.update_yaxes(gridcolor=p["grid"], zerolinecolor=p["grid"])
    return fig


def empty(theme):
    return themed(go.Figure().update_layout(
        annotations=[{"text": "No incidents match these filters",
                      "showarrow": False, "font": {"size": 14}}],
        xaxis={"visible": False}, yaxis={"visible": False}), theme)

app = Dash(__name__)
app.title = "Omaha Metro Police Activity"

app.layout = html.Div([
    dcc.Store(id="theme", data="light"),
    html.H1("Omaha Metro Police Activity"),
    html.P([
        f"{len(INCIDENTS):,} geolocated incidents, {DATE_LO} to {DATE_HI}. ",
        f"{len(CAMERAS)} ALPR cameras from OpenStreetMap. ",
        "Ralston PD publishes no feed and is not represented. ",
        "Omaha PD reports no stops or dispositions, so it is absent from the ",
        "enforcement panels below.",
    ], className="subtitle"),

    html.Div(className="controls", children=[
        html.Div([html.Label("Agency"),
                  dcc.Dropdown(AGENCIES, DEFAULT_AGENCIES, id="agencies",
                               multi=True)]),
        html.Div([html.Label("Category"),
                  dcc.Dropdown(CATEGORIES, [], id="categories", multi=True,
                               placeholder="all categories")]),
        html.Div([html.Label("Dates"),
                  dcc.DatePickerRange(id="dates", min_date_allowed=DATE_LO,
                                      max_date_allowed=DATE_HI,
                                      start_date=DATE_LO, end_date=DATE_HI)]),
        html.Div([html.Label("Show cameras"),
                  dcc.Checklist([{"label": " ALPR layer", "value": "on"}],
                                ["on"], id="show-cameras")]),
    ]),

    html.Div(id="kpis", className="kpis"),

    dcc.Graph(id="map"),
    dcc.Graph(id="timeline"),
    html.Div(className="row", children=[
        dcc.Graph(id="dispositions", className="half"),
        dcc.Graph(id="proximity", className="half"),
    ]),
    html.H2("Incidents"),
    dash_table.DataTable(id="table", page_size=10, sort_action="native",
                         style_table={"overflowX": "auto"}),
])


def filtered(agencies, categories, start, end):
    df = INCIDENTS
    if agencies:
        df = df[df["agency"].isin(agencies)]
    if categories:
        df = df[df["category"].isin(categories)]
    if start:
        df = df[df["occurred_at"] >= start]
    if end:
        df = df[df["occurred_at"] <= f"{end[:10]} 23:59:59"]
    return df


INPUTS = [Input("agencies", "value"), Input("categories", "value"),
          Input("dates", "start_date"), Input("dates", "end_date"),
          Input("theme", "data")]


@callback(Output("kpis", "children"), *INPUTS)
def update_kpis(agencies, categories, start, end, _theme):
    df = filtered(agencies, categories, start, end)
    stops = df[df["is_stop"] == 1]
    # Rate over the span the stops actually cover, not the full incident range:
    # OPD reaches back to 2022 but reports no stops at all.
    if len(stops):
        span = (stops["occurred_at"].max() - stops["occurred_at"].min()).days
        rate = f"{len(stops) / max(span, 1):.1f}"
    else:
        rate = "0"
    cards = [
        ("Incidents", f"{len(df):,}"),
        ("Vehicle stops", f"{len(stops):,}"),
        ("Stops per day", rate),
        ("Agencies", f"{df['agency'].nunique()}"),
    ]
    return [html.Div(className="kpi", children=[html.Span(v, className="kpi-value"),
                                                html.Span(k, className="kpi-label")])
            for k, v in cards]


@callback(Output("map", "figure"), *INPUTS, Input("show-cameras", "value"))
def update_map(agencies, categories, start, end, theme, show_cameras):
    df = filtered(agencies, categories, start, end)
    # A density layer over a quarter-million points saturates at any radius and
    # hides which agency is where, so plot a sample of the points instead.
    sampled = len(df) > MAP_SAMPLE
    if sampled:
        df = df.sample(MAP_SAMPLE, random_state=0)

    fig = go.Figure()
    for agency, g in df.groupby("agency", sort=False):
        fig.add_trace(go.Scattermap(
            lat=g["lat"], lon=g["lon"], mode="markers", name=agency,
            marker={"size": 4, "opacity": 0.45},
            text=g["call_type"].fillna(g["category"]).fillna(g["offense_desc"]),
            hovertemplate="%{text}<extra>" + agency + "</extra>"))
    if show_cameras and len(CAMERAS):
        fig.add_trace(go.Scattermap(
            lat=CAMERAS["lat"], lon=CAMERAS["lon"], mode="markers",
            # Amber reads against both the light and the dark basemap.
            marker={"size": 7, "color": "#ffb300"},
            name="ALPR camera",
            text=[f"{m or 'unknown make'} — {o or 'operator not tagged'}"
                  for m, o in zip(CAMERAS["manufacturer"], CAMERAS["operator"])],
            hovertemplate="%{text}<extra>ALPR</extra>"))
    title = f"{MAP_SAMPLE:,}-incident sample" if sampled else f"{len(df):,} incidents"
    fig.update_layout(map={"style": PALETTES[theme]["basemap"], "center": CENTER,
                           "zoom": 9.6},
                      height=560, margin={"r": 0, "t": 30, "l": 0, "b": 0},
                      title=title, uirevision="map",
                      legend={"x": 0.01, "y": 0.99})
    return themed(fig, theme)


@callback(Output("timeline", "figure"), *INPUTS)
def update_timeline(agencies, categories, start, end, theme):
    df = filtered(agencies, categories, start, end)
    counts = analysis.daily_counts(df)
    if counts.empty:
        return empty(theme)
    fig = px.line(counts, x="date", y="incidents", color="agency",
                  title="Incidents per day by agency")
    fig.update_layout(margin={"t": 40}, hovermode="x unified")
    return themed(fig, theme)


@callback(Output("dispositions", "figure"), *INPUTS)
def update_dispositions(agencies, categories, start, end, theme):
    # Category filter is ignored: stops are identified by is_stop, not category.
    out = analysis.stop_outcomes(filtered(agencies, None, start, end))
    if out.empty:
        return empty(theme)
    fig = px.bar(out, x="rate", y="agency", color="outcome", orientation="h",
                 barmode="group", custom_data=["stops"],
                 title="Vehicle stop outcomes",
                 labels={"rate": "share of that agency's stops"})
    fig.update_traces(hovertemplate="%{x:.1%} of %{customdata[0]:,} stops"
                                    "<extra>%{fullData.name}</extra>")
    fig.update_layout(margin={"t": 40}, xaxis_tickformat=".0%",
                      yaxis_title=None, legend_title_text=None)
    return themed(fig, theme)


@callback(Output("proximity", "figure"), *INPUTS)
def update_proximity(agencies, categories, start, end, theme):
    # Category filter is deliberately ignored: the comparison needs both stops
    # and the non-stop baseline in the same window.
    df = filtered(agencies, None, start, end)
    prox = analysis.camera_proximity(df, CAMERAS)
    if prox.empty:
        return empty(theme)
    fig = px.line(prox, x="distance_m", y="share", color="kind", markers=True,
                  title="Distance to nearest ALPR camera",
                  labels={"distance_m": "metres to nearest camera",
                          "share": "share of incidents"})
    fig.update_layout(margin={"t": 40}, yaxis_tickformat=".1%")
    return themed(fig, theme)


@callback(Output("table", "data"), Output("table", "columns"), *INPUTS)
def update_table(agencies, categories, start, end, _theme):
    cols = ["occurred_at", "agency", "category", "call_type", "disposition",
            "address"]
    df = (filtered(agencies, categories, start, end)
          .sort_values("occurred_at", ascending=False)
          .head(500)[cols])
    df = df.assign(occurred_at=df["occurred_at"].dt.strftime("%Y-%m-%d %H:%M"))
    return df.to_dict("records"), [{"name": c, "id": c} for c in cols]


if __name__ == "__main__":
    app.run(debug=True)
