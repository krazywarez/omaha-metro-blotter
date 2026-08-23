"""Refresh raw_data/boundaries.geojson, the map's geographic context.

City limits and the county line are static reference geometry, not archive
data, so this is a rare manual refresh rather than part of the daily pull.
Douglas County publishes its own city limits and boundary; Sarpy publishes
municipal boundaries under a different field name. Council Bluffs sits in
Pottawattamie County, Iowa, which publishes neither, so it is unoutlined.
"""

import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

import certifi

OUT = Path(__file__).parent / "raw_data" / "boundaries.geojson"
CTX = ssl.create_default_context(cafile=certifi.where())
# ~55 m; enough to keep the shapes honest without shipping full survey detail
TOLERANCE = 0.0005

LAYERS = [
    {"kind": "county", "name": "Douglas County", "name_field": None,
     "url": "https://services.arcgis.com/pDAi2YK0L0QxVJHj/arcgis/rest/services"
            "/Douglas_County_Boundary/FeatureServer/0"},
    {"kind": "city", "name_field": "town",
     "url": "https://dcgis.org/server/rest/services/Hosted"
            "/City_Limits_(source)_view/FeatureServer/0"},
    {"kind": "city", "name_field": "NAME",
     "url": "https://geodata.sarpy.gov/arcgis/rest/services/Cadastral"
            "/LandRecordsDynamic/MapServer/39"},
]


def fetch(layer):
    params = {"where": "1=1", "outFields": "*", "outSR": "4326",
              "maxAllowableOffset": TOLERANCE, "f": "geojson"}
    url = layer["url"] + "/query?" + urllib.parse.urlencode(params)
    # Sarpy's MapServer 403s the default Python-urllib agent
    req = urllib.request.Request(url, headers={"User-Agent": "omaha-incidents/1.0"})
    with urllib.request.urlopen(req, timeout=120, context=CTX) as r:
        return json.load(r)


def main():
    features = []
    for layer in LAYERS:
        gj = fetch(layer)
        for f in gj.get("features", []):
            field = layer["name_field"]
            name = (f.get("properties") or {}).get(field) if field else layer["name"]
            features.append({
                "type": "Feature",
                "properties": {"name": name, "kind": layer["kind"]},
                "geometry": f["geometry"],
            })
        print(f"  {len(gj.get('features', []))} from {layer['url'].split('/')[2]}")

    OUT.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features},
        separators=(",", ":")))
    verts = sum(len(r) for f in features
                for poly in ([f["geometry"]["coordinates"]]
                             if f["geometry"]["type"] == "Polygon"
                             else f["geometry"]["coordinates"])
                for r in poly)
    print(f"  wrote {OUT.relative_to(OUT.parent.parent)}: "
          f"{len(features)} features, {verts} vertices, "
          f"{OUT.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
