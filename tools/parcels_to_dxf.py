"""Export parcels intersecting an EGSA87 bbox to an AutoCAD 2007 DXF.

Writes each parcel polygon as a closed LWPOLYLINE on layer "PARCELS",
adds a KAEK label at the centroid on layer "KAEK", and draws the query
bbox on layer "BBOX". Coordinates are EGSA87 / Greek Grid (EPSG:2100).

Usage:
    python tools/parcels_to_dxf.py 470050 4452450 470300 4452700 out.dxf
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request

import ezdxf

LAYERS = {
    "LEITOURGOUN":         "https://services-eu1.arcgis.com/40tFGWzosjaLJpmn/arcgis/rest/services/GEOTEMAXIA_LEITOURGOUN_ON_gdb/FeatureServer/0",
    "ANARTHSH":            "https://services-eu1.arcgis.com/40tFGWzosjaLJpmn/arcgis/rest/services/GEOTEMAXIA_ANARTHSH_ON_gdb/FeatureServer/0",
    "PROKATARKTIKA":       "https://services-eu1.arcgis.com/40tFGWzosjaLJpmn/arcgis/rest/services/GEOTEMAXIA_PROKATARKTIKA_ON_gdb/FeatureServer/0",
}


def fetch_geometries(bbox_egsa87):
    xmin, ymin, xmax, ymax = bbox_egsa87
    geom = json.dumps({
        "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
        "spatialReference": {"wkid": 2100},
    })
    features = []
    for src_name, url in LAYERS.items():
        params = {
            "geometry": geom,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "2100",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "KAEK",
            "returnGeometry": "true",
            "outSR": "2100",
            "f": "geojson",
        }
        q = url + "/query?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(q, timeout=60) as r:
                gj = json.load(r)
        except Exception as e:
            print(f"  {src_name}: ERROR {e}", file=sys.stderr)
            continue
        for f in gj.get("features", []):
            f["properties"]["__source__"] = src_name
            features.append(f)
        print(f"  {src_name}: {len(gj.get('features', []))} features", file=sys.stderr)
    return features


def polygon_rings(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def ring_centroid(ring):
    pts = ring[:-1] if ring[0] == ring[-1] else ring
    a = 0.0
    cx = 0.0
    cy = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i][0], pts[i][1]
        x1, y1 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    a *= 0.5
    if abs(a) < 1e-9:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return cx / (6 * a), cy / (6 * a)


def build_dxf(features, bbox_egsa87, out_path):
    # AutoCAD 2007 = DXF release "AC1021" → ezdxf dxfversion "AC1021" / "R2007"
    doc = ezdxf.new(dxfversion="R2007", setup=True)
    doc.units = 6  # meters

    msp = doc.modelspace()
    if "PARCELS" not in doc.layers:
        doc.layers.add(name="PARCELS", color=3)  # green
    if "PARCELS_HOLES" not in doc.layers:
        doc.layers.add(name="PARCELS_HOLES", color=1)  # red
    if "KAEK" not in doc.layers:
        doc.layers.add(name="KAEK", color=7)
    if "BBOX" not in doc.layers:
        doc.layers.add(name="BBOX", color=1)

    # bbox rectangle
    xmin, ymin, xmax, ymax = bbox_egsa87
    msp.add_lwpolyline(
        [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)],
        close=True,
        dxfattribs={"layer": "BBOX"},
    )

    kaek_count = 0
    for feat in features:
        kaek = feat["properties"].get("KAEK", "")
        src = feat["properties"].get("__source__", "")
        rings_per_poly = polygon_rings(feat["geometry"])
        if not rings_per_poly:
            continue
        kaek_count += 1
        biggest_centroid = None
        biggest_area = -1.0
        for rings in rings_per_poly:
            for idx, ring in enumerate(rings):
                pts2d = [(p[0], p[1]) for p in ring]
                layer = "PARCELS" if idx == 0 else "PARCELS_HOLES"
                msp.add_lwpolyline(pts2d, close=True, dxfattribs={"layer": layer})
                if idx == 0:
                    cx, cy = ring_centroid(ring)
                    xs = [p[0] for p in ring]
                    ys = [p[1] for p in ring]
                    area = (max(xs) - min(xs)) * (max(ys) - min(ys))
                    if area > biggest_area:
                        biggest_area = area
                        biggest_centroid = (cx, cy)
        if biggest_centroid:
            text = msp.add_text(
                kaek,
                dxfattribs={"layer": "KAEK", "height": 2.0},
            )
            text.set_placement(biggest_centroid, align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    doc.saveas(out_path)
    return kaek_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xmin", type=float)
    ap.add_argument("ymin", type=float)
    ap.add_argument("xmax", type=float)
    ap.add_argument("ymax", type=float)
    ap.add_argument("out", help="output .dxf path")
    args = ap.parse_args()
    bbox = (args.xmin, args.ymin, args.xmax, args.ymax)
    print(f"Querying parcels intersecting EGSA87 bbox {bbox} ...", file=sys.stderr)
    features = fetch_geometries(bbox)
    n = build_dxf(features, bbox, args.out)
    print(f"Wrote {n} parcels to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
