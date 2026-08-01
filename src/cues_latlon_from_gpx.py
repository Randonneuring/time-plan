"""Extract waypoints (rtept objects) from a GPX file.

In RWGPS, a 'route' is different from a 'track'. A 'route' contains
all the labeled points and not all the breadcrumbs between.  In a route,
we can find rtept entries that will include landmarks like summits and
controls, as they appear in a cuesheet, associated with latitude and longitude
that we can use for matching to an "as ridden" track with timestamps.
"""
import gpxpy
import csv
import argparse
import sys

import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("gpx", help="Path to GPX route file")
    parser.add_argument("output", help="Path to output CSV file")
    args = parser.parse_args()

    # Open gpx file
    try:
        if args.gpx == "-":
            args.f_gpx = sys.stdin
        else:
            args.f_gpx = open(args.gpx, 'r', encoding="utf-8-sig")
    except Exception as e:
        print(f"Error opening GPX file: {e}", file=sys.stderr)
        sys.exit(1)


    # Open output file
    try:
        if args.output == "-":
            args.f_output = sys.stdout
        else:
            args.f_output = open(args.output, 'w', encoding="utf-8-sig")
    except Exception as e:
        print(f"Error opening output file: {e}", file=sys.stderr)
        sys.exit(1)

    return args


def load_gpx(f) -> list[dict]:
    result = []
    gpx = gpxpy.parse(f)
    for route in gpx.routes:
        for point in route.points:
            result.append({"Cue": point.comment, "Latitude": point.latitude, "Longitude": point.longitude})
    return result


if __name__ == "__main__":
    args = cli()
    gpx_route = load_gpx(args.f_gpx)
    writer = csv.DictWriter(args.f_output, fieldnames=gpx_route[0].keys())
    writer.writeheader()
    writer.writerows(gpx_route)
