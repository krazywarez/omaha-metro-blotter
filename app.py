from dash import Dash, html, dcc, callback, Output, Input, dash_table
import plotly.express as px
import pandas as pd
import sqlite3

# Connect to database and query all incidents
connection = sqlite3.connect("./raw_data/ingress.db")
cursor = connection.cursor()
query = "SELECT * FROM incidents;"
df = pd.read_sql_query(query, connection).sort_values(by="description")

# Create custom YEAR column to use in dropdown
df['year'] = df['date'].str[-4:]

# Configure HTML layout
app = Dash(__name__)
app.layout = html.Div(children = [
    html.H1(children="Omaha Police Incidents", style={"textAlign":"center"}),
    html.Div([
        html.H2(children="Totals per Category and Year", style={"textAlign":"center"}),
        dcc.Dropdown(df.sort_values("description").description.unique(), "INJURY", id="dropdown"),
        dcc.Dropdown(df.sort_values("year").year.unique(), "2023", id="year-dropdown"),
        html.Hr(),
        dash_table.DataTable(data=df.to_dict("records"), page_size=5, id="table"),
        html.H2(children="Map of Incidents per Category and Year", style={"textAlign":"center"}),
        dcc.Graph(id="map-graph")
    ])
])

# Create table
@callback(
    Output("table", "data"),
    Input("dropdown", "value"),
    Input("year-dropdown", "value")
)
def update_table(description, year):
    dff = df[df.year == year]
    dff = dff.reset_index()
    dff = dff[dff.description == description]
    return dff.to_dict("records")

# Create map
@callback(
    Output("map-graph", "figure"),
    Input("dropdown", "value"),
    Input("year-dropdown", "value")
)
def update_map(description, year):
    dff = df[df.year == year]
    dff = dff.reset_index()
    dff = dff[dff.description == description]

    fig = px.scatter_mapbox(
        dff,
        lat="lat",
        lon="lon",
        color="description",
        hover_name="description",
        hover_data=["date", "time"],
        title="Incident Count by Coordinates",
        center={"lat": 41.257160, "lon": -95.995102},
        zoom=10
    )
    
    fig.update_layout(showlegend=False)
    fig.update_layout(mapbox_style="open-street-map")
    fig.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0})
    fig.update_layout(mapbox_bounds={"west": -180, "east": -50, "south": 20, "north": 90})

    return fig

if __name__ == "__main__":
    app.run(debug=True)
