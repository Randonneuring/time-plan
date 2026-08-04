"""Match recorded points in a RWGPS trip to landmarks in a RWGPS route.
Roughly divided into two concerns:
  - Extract the relevant information from RWGPS route and trip objects.
    This is specific to objects obtained from the RWGPS API, but its
    results could be duplicated by analysis of TCX or even FIT files.
  - Correlate the two, producing a table that maps landmarks to
    data from the trip record.  This part should work the same regardless
    of how the data is obtained, but does depend on certain assumptions
    about the point data.

Although we mean to separate the former from the latter, there are some
assumptions in the correlation that come from what we we know about GPS
recording represented in the trip record, especially that we assume
points in the trip record are reasonably well spaced.  A long "stutter"
of points while still (e.g., while getting lunch along the route) will
break the assumption that "n closest points" is enough to get a mix of
points from multiple passages near a landmark.


RWGPS significant cues appear some as track points (identifiable by type, e.g., "Summit")
  and some as Points of Interest.  Points of interest that appear as cues are identifiable because
  they have a "distances" array, which in examples I have inspected are always singletons.

   Planned approach
   Filter track points in route by kind, adding begin and end of route because those don't
   necessarily appear as cues.
   Filter points of interest by presences of "distances" array.
   Sort both arrays by distance.  Call this the "route point" array.
   Create one KD tree from trip points.
   Matches to route point arrays are found in order, keeping an auxilliary
   variable "bonus" which begins at zero and is updated when distance to a match
   exceeds route point distance.
   Use constants
       epsilon:  How far off track can a valid match be?  Initially 0.5km
   For each route point,
       let D = route point distance
       candidates is set of track points within epsilon of route point location,
       at distance between D - epsilon and D + bonus + epsilon.
   Note: Working with latlon, epsilon may not be so constant, and can
   vary between lat and lon.

    Pre-filter runs of near-equal distance so that instead of picking points within
    epsilon of landmark, we pick N closest points to guarantee all points within epsilon
    provided the landmark was passed less than 3 times (enough for out-and-back and
    lollipop routes).   For this we need a delta that is
    - large enough that noise around a non-moving bike is not registered as movement
    - small enough that at least one point within epsilon of a landmark is retained (?)
      (although we could probably make 'closest point' selection robust enough to
      not depend on this)
"""

import numpy as np
from pykdtree.kdtree import KDTree
import datetime as dt

import logging
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

DIRECTIONAL_CUES = set(["Left", "Right", "Slight Left", "Slight Right", "Sharp Left", "Sharp Right",
                        "Straight", "U-Turn"])
PAUSE_THRESHOLD_METERS = 5

# The trip points structure is a triple of parallel arrays.  The first
# list is lat/lon pairs, the second is integer distances in meters, the third
# is timestamps, which are Unix epoch times (integer seconds since January 1, 1970).
# This requires a minimum of processing from RWGPS returned structure and makes
# it easy to create a KD tree.
#
trip_points_t = tuple[list[tuple[float, float]], list[int], list[int]]
#                                latlon                      dist       times

# The route points structure is a list of tuples rather than a tuple of lists,
# so that it can be easily sorted.
#
route_points_t = list[tuple[float, float], float, str]
#                     latlon                          dist         textual description

def route_points_from_rwgps(route: dict) -> route_points_t:
    """Extract the route points from a route object returned by the RWGPS API"""
    result = []
    # Include cues but excluding directional cues
    course_points = route.get("course_points", [])
    assert course_points, "No course points in route"
    for point in course_points:
        if point["t"] not in DIRECTIONAL_CUES:
            result.append((point["y"], point["x"], point["d"], point["t"] + ":" + point["n"] ))
    # Include landmarks with distances (that is, POIs that are on course)
    pois = route.get("points_of_interest", [])
    for point in pois:
        if point.get("distances", []):
            # This is a POI that is on the course
            result.append((point["lat"], point["lng"], point["distances"][0], point["type_name"] + ":" + point["name"]))
    return sorted(result, key=lambda x: x[2])

def trip_points_from_rwgps(trip: dict) -> trip_points_t:
    """Extract the trip points from a trip object returned by the RWGPS API.
    We filter to remove stuttering or "wandering" during pauses, based on
    GPS accuracy being within a bound defined by PAUSE_THRESHOLD_METERS.
    """
    points_array = []  # List of lat, lon pairs
    distances_array = [] # Parallel list of distances in meters
    timestamps_array = [] # Parallel list of times as Unix epoch seconds

    count_skipped = 0
    count_kept = 0


    # Initial point is the departure point and time at distance 0
    lat_lon = (trip["first_lat"], trip["first_lng"])
    dist = 0
    timestamp = dt.datetime.fromisoformat(trip["departed_at"]).timestamp()

    points_array.append(lat_lon)
    distances_array.append(dist)
    timestamps_array.append(timestamp)

    # Same pattern for each subsequent point.
    for point in trip["track_points"]:
        if point["d"] < dist + PAUSE_THRESHOLD_METERS:
            log.debug(f"Skipping point {point['d']} close to prior distance {dist}")
            count_skipped += 1
            continue
        count_kept += 1
        lat_lon = (point["y"], point["x"])
        dist = point["d"]
        timestamp = point["t"]
        points_array.append(lat_lon)
        distances_array.append(dist)
        timestamps_array.append(timestamp)
    log.debug(f"Kept {count_kept} points, skipped {count_skipped}")
    return (points_array, distances_array, timestamps_array)



