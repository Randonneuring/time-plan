"""Extract relevant time-stamps from GPX trace using latitude and longitude.

The main logic of this has been reworked into flask app in route_trip_match.py.
This module should be discarded if/when route_trip_match is incorporated in
a CLI application.   Retained for now just for testing.
"""

import geopy.distance
import gpxpy
import datetime as dt
from pykdtree.kdtree import KDTree
import numpy as np
import csv
import argparse
import pathlib
import sys

import logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

DELTA_LOC_KM = 0.2  # "At" landmark if within 200 meters

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
    """Returns the landmarks file as a list.  Requires landmarks file to
    be CSV with Cue,Latitude,Longitude, as extracted by extract_route_waypoints.py.
    """
    reader = csv.DictReader(f)
    as_list = list(reader)
    first_row = as_list[0]
    assert "Cue" in first_row and "Latitude" in first_row and "Longitude" in first_row, \
         "Landmarks file does not contain the expected columns"
    return as_list



def load_gpx(f) -> list[gpxpy.gpx.GPXTrackPoint]:
    result = []
    gpx = gpxpy.parse(f)
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                result.append(point)
    return result

def brute_force_match(landmarks: list[dict], gpx_points: list[gpxpy.gpx.GPXTrackPoint]):
    """Brute force match each landmark to a GPX point
    (The simplest thing that could possibly work.)
    Adds a 'time' column to each landmark, with the time stamp of the closest GPX point.
    SLOW as expected ... left here for possible comparison to kd-tree approach.
    """
    for landmark in landmarks:
        landmark['time'] = None
        landmark['closest'] = 999_999_999.0
    for point in gpx_points:
        for landmark in landmarks:
              distance = geopy.distance.distance(
                  (landmark['Latitude'], landmark['Longitude']),
                  (point.latitude, point.longitude)
              ).kilometers
              if distance < landmark['closest']:
                  log.debug(f"Landmark {landmark['Cue']} is {distance} km away from GPX point")
                  log.debug(f"Landmark is at latlon {landmark['Latitude']}, {landmark['Longitude']} ")
                  log.debug(f"GPX point is at latlon {point.latitude}, {point.longitude}")
                  landmark['closest'] = distance
                  landmark['time'] = point.time.isoformat()
    # Result is in landmarks, with a 'time' column added
    return landmarks

def kd_tree_match(landmarks: list[dict], gpx_points: list[gpxpy.gpx.GPXTrackPoint]):
    """Use a kd-tree to match each landmark to a GPX point.  All points
    go into tree for fast nearest-neighbor search.  (Currently just one point; we
    could pick three to avoid any dirty data issues, but one seems satisfactory.)
    Scan forward and backward from each landmark for close points to find
    time spent at landmark.
    """
    # For KD-tree, we need to convert lat/lon to a tuple of floats
    # Indexes will be parallel to gpx_points
    points = np.array([(float(point.latitude), float(point.longitude)) for point in gpx_points])
    tree = KDTree(points)
    # First cut:  What is closest point to each landmark?
    for landmark in landmarks:
        latlon = (float(landmark['Latitude']), float(landmark['Longitude']))
        target = np.array([latlon])
        closest_dist, closest_index = tree.query(target, 1)
        gpx_closest = gpx_points[closest_index[0]]
        closest_point = [gpx_closest.latitude, gpx_closest.longitude]
        true_dist = geopy.distance.distance(latlon, closest_point).kilometers
        log.debug(f"Landmark {landmark['Cue']} is {closest_dist} ({true_dist:2.2} km) from GPX point")
        # Approach and depart indexes
        arrival_index = closest_index[0]
        arrival_dist = true_dist
        depart_index = closest_index[0]
        depart_dist = true_dist
        while arrival_index > 0:
            trial_index = arrival_index - 1
            approach_point = [gpx_points[trial_index].latitude, gpx_points[trial_index].longitude]
            trial_dist = geopy.distance.distance(latlon, approach_point).kilometers
            if trial_dist < DELTA_LOC_KM:
                arrival_index = trial_index
            else:
                break
        while depart_index < len(gpx_points) - 1:
            trial_index = depart_index + 1
            depart_point = [gpx_points[trial_index].latitude, gpx_points[trial_index].longitude]
            trial_dist = geopy.distance.distance(latlon, depart_point).kilometers
            if trial_dist < DELTA_LOC_KM:
                depart_index = trial_index
            else:
                break
        arrival_time = gpx_points[arrival_index].time.astimezone().strftime("%H:%M")
        depart_time = gpx_points[depart_index].time.astimezone().strftime("%H:%M")
        landmark['arrival_time'] = arrival_time
        landmark['depart_time'] = depart_time
        print(f"{arrival_time} to {depart_time}\t{landmark['Cue']}")





if __name__ == "__main__":
    args = cli()
    entries = load_landmarks(args.f_landmarks)
    gpx_points = load_gpx(args.f_gpx)
    # brute_force_match(entries, gpx_points)
    # writer = csv.DictWriter(args.f_output, fieldnames=entries[0].keys())
    # writer.writeheader()
    # writer.writerows(entries)
    kd_tree_match(entries, gpx_points)
