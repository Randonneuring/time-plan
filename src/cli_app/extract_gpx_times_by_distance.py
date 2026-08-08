"""Extract relevant time-stamps from GPX trace

Status:  Not currently used in flask app.
Could be useful if the main logic is reused as a CLI app.  In that case,
we would want to make the returned object closer to the form that we
extract from the RWGPS API.
"""

import geopy.distance
import gpxpy
import datetime as dt
import csv
import argparse
import pathlib
import sys

import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("gpx", help="Path to GPX file")
    parser.add_argument("landmarks", help="Path to CSV file with landmarks")
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

    # Open landmarks file
    try:
        if args.landmarks == "-":
            args.f_landmarks = sys.stdin
        else:
            args.f_landmarks = open(args.landmarks, 'r', encoding="utf-8-sig")
    except Exception as e:
        print(f"Error opening landmarks file: {e}", file=sys.stderr)
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


def load_landmarks(f):
    """Returns the landmarks file as a list, adding a "Km" column if not already present.
    The original could have "Mi"  (miles) or, if it is from a RWGPS cuesheet in CVS form,
    it could have either Distance (km) From Start or Distance (mi) From Start.
    Error if it contains none of those.
    """
    reader = csv.DictReader(f)
    as_list = list(reader)
    first_row = as_list[0]
    if 'Km' not in first_row:
        if 'Distance (km) From Start' in first_row:
            for row in as_list:
                row['Km'] = float(row['Distance (km) From Start'])
        elif 'Distance (miles) From Start' in first_row:
            for row in as_list:
                row['Km'] = float(row['Distance (miles) From Start']) * 1.609344
        elif 'Mi' in first_row:
            for row in as_list:
                row['Km'] = floa(row['Mi']) * 1.609344
        else:
            raise ValueError(f"Landmarks file does not contain a 'Km' column: {first_row}")
    return as_list



def load_gpx(f) -> list[gpxpy.gpx.GPXTrackPoint]:
    result = []
    gpx = gpxpy.parse(f)
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                result.append(point)
    return result


def augment_landmarks(landmarks: list[dict], gpx_points: list[gpxpy.gpx.GPXTrackPoint]) -> list[dict]:
    """Augment each landmark with the time stamp of the first point exceeding accumulated distance in GPX trace"""
    result = []
    accumulated_distance = 0.0
    gpx_index = 0

    for landmark in landmarks:
        target_distance = float(landmark.get('Km'))

        while gpx_index < len(gpx_points) - 1 and accumulated_distance <= target_distance:
            current_point = gpx_points[gpx_index]
            next_point = gpx_points[gpx_index + 1]

            distance = geopy.distance.distance(
                (current_point.latitude, current_point.longitude),
                (next_point.latitude, next_point.longitude)
            ).kilometers

            accumulated_distance += distance
            gpx_index += 1

        augmented_landmark = landmark.copy()
        if gpx_index < len(gpx_points):
            augmented_landmark['time'] = gpx_points[gpx_index].time.isoformat()
            augmented_landmark['local_time'] = gpx_points[gpx_index].time.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            augmented_landmark['accumulated_distance'] = accumulated_distance

        result.append(augmented_landmark)

    return result


if __name__ == "__main__":
    args = cli()
    entries = load_landmarks(args.f_landmarks)
    gpx_points = load_gpx(args.f_gpx)
    # print(gpx_points)
    augmented = augment_landmarks(entries, gpx_points)
    writer = csv.DictWriter(args.f_output, fieldnames=augmented[0].keys())
    writer.writeheader()
    writer.writerows(augmented)
