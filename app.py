from dash import Dash, html, dcc, callback, Output, Input, dash_table
import plotly.express as px
import pandas as pd
import numpy as np
import sqlite3
from datetime import datetime, date

# Connect to database and query all incidents
connection = sqlite3.connect("./raw_data/ingress.db")
cursor = connection.cursor()
query = "SELECT * FROM incidents;"
df = pd.read_sql_query(query, connection).sort_values(by="description")

# Replace empty cells with NaN
df = df.replace(r'^\s*$', np.nan, regex=True)

# Convert date column to datetime
# TODO: Create a combined datetime column in the db to load/convert here
df["date"] = pd.to_datetime(df["date"])

# Configure HTML layout
app = Dash(__name__)
app.layout = html.Div(children = [
    html.H1(children="Omaha Crime Mapping & Analysis", style={"textAlign":"center"}),
    html.Div([
        html.Div(className="flex",
            children = [
                html.P("Crime:"),
                dcc.Dropdown(df.sort_values("description").description.unique(), "INJURY", id="dropdown"),
            ]
        ),
        html.Div(className="flex",
            children = [
                html.P("Date Range:"),
                dcc.DatePickerRange(
                    id='date-picker-range',
                    min_date_allowed=date(2015, 1, 1),
                    max_date_allowed=date(2023, 12, 31),
                    start_date=date(2015, 1, 1),
                    end_date=date(2023,12,31)
                ),
            ]
        ),
        dcc.Graph(id="map-graph"),
        dcc.Graph(id="line-graph"),
        dash_table.DataTable(data=df.to_dict("records"), page_size=5, id="table")
    ])
])

# Create map
@callback(
    Output("map-graph", "figure"),
    Input("dropdown", "value"),
    Input('date-picker-range', 'start_date'),
    Input('date-picker-range', 'end_date')
)
def update_map(description, start_date, end_date):
    dff = df[(df['date'] > start_date) & (df['date'] < end_date)]
    dff = dff.reset_index()
    dff = dff[dff.description == description]

    fig = px.scatter_mapbox(
        dff,
        lat="lat",
        lon="lon",
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

# Create line graph
@callback(
    Output("line-graph", "figure"),
    Input("dropdown", "value"),
    Input('date-picker-range', 'start_date'),
    Input('date-picker-range', 'end_date')
)
def update_line(description, start_date, end_date):
    dff = df[(df['date'] > start_date) & (df['date'] < end_date)]
    dff = dff.reset_index()
    dff = dff[dff.description == description]
    dff = dff.groupby(by="date").count()
    dff = dff.reset_index()

    fig = px.line(
        dff,
        x="date",
        y="description",
    )

    return fig

# Create table
@callback(
    Output("table", "data"),
    Input("dropdown", "value"),
    Input('date-picker-range', 'start_date'),
    Input('date-picker-range', 'end_date')
)
def update_table(description, start_date, end_date):
    dff = df[(df['date'] > start_date) & (df['date'] < end_date)]
    dff = dff.reset_index()
    dff = dff[dff.description == description]
    return dff.to_dict("records")

if __name__ == "__main__":
    app.run(debug=True)
