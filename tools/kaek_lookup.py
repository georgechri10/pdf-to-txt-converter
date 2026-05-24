"""Look up KAEK (Greek cadastral codes) for parcels intersecting an EGSA87 bbox.

Source: ArcGIS FeatureServer endpoints used by https://maps.ktimatologio.gr/
(Hellenic Cadastre — ktimatologio.maps.arcgis.com).

The public INSPIRE WFS (gis.ktimanet.gr/inspire/.../CadastralParcel) only
covers the "Operating Cadastre" (Λειτουργούν Κτηματολόγιο); the FeatureServer
endpoints below additionally cover Posting (Ανάρτηση) and Preliminary
(Προκαταρκτικά) phases, which is necessary for areas not yet operational.

Usage:
    python tools/kaek_lookup.py 470050 4452450 470300 4452700
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request

LAYERS = {
    "LEITOURGOUN":         "https://services-eu1.arcgis.com/40tFGWzosjaLJpmn/arcgis/rest/services/GEOTEMAXIA_LEITOURGOUN_ON_gdb/FeatureServer/0",
    "ANARTHSH":            "https://services-eu1.arcgis.com/40tFGWzosjaLJpmn/arcgis/rest/services/GEOTEMAXIA_ANARTHSH_ON_gdb/FeatureServer/0",
    "PROKATARKTIKA":       "https://services-eu1.arcgis.com/40tFGWzosjaLJpmn/arcgis/rest/services/GEOTEMAXIA_PROKATARKTIKA_ON_gdb/FeatureServer/0",
    "APOKLEISTIKES_LEIT":  "https://services-eu1.arcgis.com/40tFGWzosjaLJpmn/arcgis/rest/services/GEOTEMAXIA_APOKLEISTIKES_ON_gdb/FeatureServer/0",
    "DOULEIES_LEIT":       "https://services-eu1.arcgis.com/40tFGWzosjaLJpmn/arcgis/rest/services/GEOTEMAXIA_DOULEIES_ON_gdb/FeatureServer/0",
}


def query_layer(layer_url, bbox_egsa87, spatial_rel="esriSpatialRelIntersects", timeout=45):
    xmin, ymin, xmax, ymax = bbox_egsa87
    geom = json.dumps({
        "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
        "spatialReference": {"wkid": 2100},
    })
    params = {
        "geometry": geom,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "2100",
        "spatialRel": spatial_rel,
        "outFields": "KAEK",
        "returnGeometry": "false",
        "f": "json",
    }
    q = layer_url + "/query?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(q, timeout=timeout) as r:
        return json.load(r)


def lookup(bbox_egsa87):
    per_layer = {}
    for name, url in LAYERS.items():
        try:
            d = query_layer(url, bbox_egsa87)
            per_layer[name] = [f["attributes"].get("KAEK") for f in d.get("features", [])]
        except Exception as e:
            per_layer[name] = {"error": str(e)}
    all_kaek = sorted({k for v in per_layer.values()
                       if isinstance(v, list)
                       for k in v if k})
    return {
        "bbox_egsa87": list(bbox_egsa87),
        "kaek_list": all_kaek,
        "count": len(all_kaek),
        "method_used": "ArcGIS FeatureServer query (services-eu1.arcgis.com)",
        "source": "maps.ktimatologio.gr web map (ktimatologio.maps.arcgis.com)",
        "per_layer": per_layer,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xmin", type=float)
    ap.add_argument("ymin", type=float)
    ap.add_argument("xmax", type=float)
    ap.add_argument("ymax", type=float)
    args = ap.parse_args()
    result = lookup((args.xmin, args.ymin, args.xmax, args.ymax))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
