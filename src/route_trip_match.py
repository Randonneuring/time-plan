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
from zoneinfo import ZoneInfo

from haversine import haversine, Unit as hv_Unit

import logging
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344

"""Search parameter constants in meters"""
MAX_SEGMENT_BONUS = 1000     # Allow up to 1km extra between controls (and more if needed)
MAX_LANDMARK_MISS = 1000     # Match point up to 1000 meters from landmark
NUM_CANDIDATES = 20          # Enough trip points to ensure getting all passages

"""Including or omitting cues based on type. """
DIRECTIONAL_CUES = set(["Left", "Right", "Slight Left", "Slight Right", "Sharp Left", "Sharp Right",
                        "Straight", "Uturn"])
INFO_NOT_LANDMARK = set(["Danger", "Caution"])
IGNORE_CUES = DIRECTIONAL_CUES | INFO_NOT_LANDMARK

# Have we paused or are we still moving?   GPS coordinates can
# wander by small amounts while we are paused.  This is used to
# remove redundant readings from the trace.
PAUSE_THRESHOLD_METERS = 5


# The trip points structure is a triple of parallel arrays.  The first
# list is lat/lon pairs, the second is integer distances in meters, the third
# is timestamps, which are Unix epoch times (integer seconds since January 1, 1970).
# This requires a minimum of processing from RWGPS returned structure and makes
# it easy to create a KD tree.
trip_points_t = tuple[list[tuple[float, float]], list[int], list[int]]
#                                latlon                      dist       times

# The route points structure is a list of tuples rather than a tuple of lists,
# so that it can be easily sorted.
route_points_t = list[tuple[tuple[float, float], float, str, str]]
# [((lat, lon), dist, cue_text, type), ...]


def route_points_from_rwgps(route: dict, options: dict[str, bool]) -> route_points_t:
    """Extract the route points from a route object returned by the RWGPS API"""
    # Cues: At least controls, sometimes also turns
    result = []
    # Begin and end are implicit route points
    result += [((route["first_lat"], route["first_lng"]), 0, "Start", "Start")]
    result += [((route["last_lat"], route["last_lng"]), route["distance"], "Finish", "Finish")]

    ignore = set() if options["cues"] else IGNORE_CUES
    result += cues_from_rwgps(route, ignore)
    # Include landmarks with distances (that is, POIs that are on course)
    waypoints = waypoints_from_rwgps(route)
    result += waypoints

    # Selected mileposts
    if options["miles_5"]:
        probes = miles_meters_probe_points(5, route["distance"])
        result += mileposts_from_rwgps(route, probes)
    elif options["miles_10"]:
        probes = miles_meters_probe_points(10, route["distance"])
        result += mileposts_from_rwgps(route, probes)

    if options["km_10"]:
        probes = kilometers_probe_points(10, route["distance"])
        result += mileposts_from_rwgps(route, probes)

    return sorted(result, key=lambda x: x[1])


def cues_from_rwgps(route: dict, ignore: set[str]) -> route_points_t:
    """Extract selected cues"""
    result = []
    # Include cues but excluding directional cues & some info cues
    course_points = route.get("course_points", [])
    assert course_points, "No course points in route"
    log.debug(f"Route has {len(course_points)} course points")

    for point in course_points:
        log.debug(f"Considering course point {point}")
        kind = point["t"]
        if kind not in ignore:
            log.debug(f"Keeping course point of type |{kind}| at distance {point['d']}")
            text = point['n']
            result.append(((point["y"], point["x"]), point["d"], text, kind ))
        else:
            log.debug(f"Ignoring cue of type |{point['t']}| at distance {point['d']}")
    return result


def miles_meters_probe_points(mile_intervals: int, total_meters: int) -> list[int]:
    """Meters distances corresponding to mile intervals."""
    probes   = []
    start_milepost = mile_intervals * METERS_PER_MILE  # Start mileposts here
    stop_milepost = total_meters - mile_intervals * METERS_PER_MILE  # Stop before here
    current_mile = mile_intervals
    current_meters = start_milepost
    while current_meters <= stop_milepost:
        probes.append(current_meters)
        current_mile += mile_intervals
        current_meters += mile_intervals * METERS_PER_MILE
    return probes


def kilometers_probe_points(km_intervals: int, total_meters: int)    -> list[int]:
    """Kilometers distances corresponding to km intervals."""
    probes = []
    start_meters = km_intervals * 1000
    stop_meters = total_meters - km_intervals * 1000
    current_meters = start_meters
    while current_meters <= stop_meters:
        probes.append(current_meters)
        current_meters += km_intervals * 1000
    return probes


def mileposts_from_rwgps(route: dict, probes: list[int]) -> route_points_t:
    """Given a route and a list of meter distance probes, return a list of
    synthetic route points ((lat, lon), distance, text) as if they were
    cues or waypoints.  We use track_points rather than course_points so that we
    are following the route between turns.
    """
    result = []
    track_points = route.get("track_points", [])
    assert track_points, "No course points in route"
    course_index = 1  # We need to be able to index *prior* point
    for probe in probes:
        while course_index < len(track_points) and track_points[course_index]["d"] < probe:
            course_index += 1
        # Now course index is first point with distance >= probe
        lat_before, lon_before = track_points[course_index - 1]["y"], track_points[course_index - 1]["x"]
        lat_after, lon_after = track_points[course_index]["y"], track_points[course_index]["x"]
        dist_before = track_points[course_index - 1]["d"]
        dist_after = track_points[course_index]["d"]
        text = ""
        lat = interpolate(dist_before, probe, dist_after, lat_before, lat_after)
        lon = interpolate(dist_before, probe, dist_after, lon_before, lon_after)
        result.append(((lat, lon), probe, text, "Δ"))
    return result


def interpolate(x_before: int, x_probe: int, x_after: int, y_before: float, y_after: float) -> float:
    """Linear interpolation between two points"""
    return y_before + (x_probe - x_before) * (y_after - y_before) / (x_after - x_before)


def waypoints_from_rwgps(route: dict) -> route_points_t:
    """Waypoints are POIs associated with distances on route"""
    result = []
    pois = route.get("points_of_interest", [])
    log.debug(f"Route has {len(pois)} POIs")
    for point in pois:
        if point.get("distances", []) and point.get("type_name", "") not in INFO_NOT_LANDMARK:
            # This is a POI that is on the course
            log.debug(f"Keeping POI {point['name']} of type {point['type_name']}")
            result.append(((point["lat"], point["lng"]), point["distances"][0],
                           point['name'], point["type_name"]))
        else:
            log.debug(f"Ignoring POI {point['name']} of type {point['type_name']}\n {point}")
    return result


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
        if "y" not in point or "x" not in point or "d" not in point:
            log.debug(f"Skipping point {point} for missing components")
            count_skipped += 1
            continue
        if point["d"] < dist + PAUSE_THRESHOLD_METERS:
            # log.debug(f"Skipping point {point['d']} close to prior distance {dist}")
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
    assert len(points_array) == len(distances_array) == len(timestamps_array)
    return (points_array, distances_array, timestamps_array)



def matches(route: route_points_t, trip: trip_points_t) -> list[dict]:
    """Return a list of dictionaries associating each route point with
    a segment of the trip point data within epsilon of the route point.
    Trip distance disambiguates multiple passages through the same point,
    e.g. on lollipop and out-and-back routes, assuming trip points are
    widely enough spaced that a set of CANDIDATES points can't all be
    from one passage.
    If no trip point qualifies, we include the route point with a
    negative indicator (found=False) and default values.
    Dicts in the returned tuples contain all the information we can place
    into the result table, in raw form for further processing and formatting
    (e.g., times in Unix epoch seconds rather than some human-readable form).
    """
    trip_latlons, trip_dists, trip_times = trip
    kdtree = KDTree(np.array(trip_latlons))
    matches = []
    bonus_meters = 0    # Accumulated extra distance from going off course
    for route_point in route:
        log.debug(f"Considering {route_point}")
        latlon, dist, text, kind = route_point

        # Default if we don't find a match.
        entry = {"found": False, "dist": dist, "time": "", "latlon": latlon,
                 "text": f"No trip points within {MAX_LANDMARK_MISS} meters of {text}",
                 "kind": kind, "deviation": MAX_LANDMARK_MISS}

        # Select enough close points to have representatives of multiple passages
        # by landmark in case of loop, out-and-back, or lollipop routes
        _, candidates_l = kdtree.query(np.array([np.array(latlon)]), NUM_CANDIDATES)
        candidates = candidates_l[0]
        # Result is a list of indices into trip_latlons, trip_dists, and trip_times.
        log.debug(f"Candidates for {text} are {candidates}")

        # Filtering: We eliminate points for two reasons:
        # 1. Distance indicates this is not the appropriate
        #    passage through the point (e.g., out-and-back or
        #    lollipop course, as well as distinguishing start
        #    from end of loop course).  Cheap.
        # 2. Distance is too far from the landmark.  Note LatLon
        #    used by KD tree is only a rough approximation of distance,
        #    which requires a more expensive haversine calculation, and
        #    moreover the k closest points may all be far from the
        #    landmark, so this could filter more and possibly even
        #    all the candidate points.
        
        filtered = []
        bound_low = dist - MAX_LANDMARK_MISS
        bound_high = dist + MAX_LANDMARK_MISS + bonus_meters
        # First filter on distance.  Separate step because we might
        # keep all the candidates if we went way off course and racked
        # up a bunch of bonus meters.
        for candidate in candidates:
            # log.debug(f"Considering candidate {candidate} among {len(trip_dists)}")
            if bound_low <= trip_dists[candidate] <= bound_high:
                log.debug(f"Keep {candidate}: {bound_low} <= {trip_dists[candidate]} <= {bound_high}")
                filtered.append(candidate)

        # Use filtered list only if non-empty
        if len(filtered) > 0:
            log.debug(f"Found {len(filtered)} candidates out of {len(candidates)} for {text} within {bound_low}-{bound_high} meters")
            candidates = filtered
            log.debug(f"Filtered to {candidates}")
        else:
            log.warning(f"No candidates for {text} within {bound_low}-{bound_high} meters")
        # Candidates still guaranteed to be non-empty

        # Second pass filters on closeness to landmark, while
        # selecting closest.   If we reject all, the default entry set above will be used.
        closest = candidates[0]
        closest_dist = haversine(latlon, trip_latlons[closest], unit=hv_Unit.METERS)
        found = False
        for candidate in candidates:
            candidate_latlon = trip_latlons[candidate]
            candidate_dist_to_landmark = haversine(latlon, candidate_latlon, unit=hv_Unit.METERS)
            if candidate_dist_to_landmark <= MAX_LANDMARK_MISS:
                found = True
                if candidate_dist_to_landmark < closest_dist:
                    closest = candidate
                    closest_dist = candidate_dist_to_landmark
        log.debug(f"Chose {closest} for {text} within {closest_dist:.2f} meters")

        if found:
            log.debug(f"Found closest candidate {closest} for {text} within {closest_dist:.2f} meters")
            closest_time = trip_times[closest]
            arrival_time, depart_time = time_paused(trip, closest)
            entry = {"found": True, "dist": dist, "time": closest_time,
                     "arrival_time": arrival_time, "departure_time": depart_time,
                     "latlon": trip_latlons[closest], "text": text,
                     "deviation": closest_dist,
                     "kind": kind}
            bonus_meters = max(bonus_meters, trip_dists[candidate] - dist)
            #FIXME: Should we always reset to current bonus meters?
        matches.append(entry)

    return matches

def time_paused(trip: trip_points_t, closest: int) -> tuple[int, int]:
    """Given one 'closest' point, look back and forward to find time
    paused within delta meters of that point, returning both as
    unix epoch seconds.
    """
    EPSILON = 500 # Meters that GPS can wander
    trip_latlons, trip_dists, trip_times = trip
    first = closest
    first_time = trip_times[first]
    while first > 0:
        deviation = haversine(trip_latlons[first], trip_latlons[closest], unit=hv_Unit.METERS)
        if deviation <= EPSILON:
            first_time = trip_times[first]
            first -= 1
        else:
            break
    last = closest
    last_time = trip_times[last]
    while last < len(trip_times) - 1:
        deviation = haversine(trip_latlons[last], trip_latlons[closest], unit=hv_Unit.METERS)
        if deviation <= EPSILON:
            last_time = trip_times[last]
            last += 1
        else:
            break

    return first_time, last_time


def humanize_matches_rwgps(matches: list[dict], trip_struc: dict):
    """Decorate each match struct with human-readable text and time stamp.
    Modifies each dict structure (does not return a new list).
    Specialized to RWGPS API trip structure; a similar function could be
    created for FIT or TCX files.

    FIXME: Am I mixing in too much formatting that should be in the output functionality
        for web page and CSV?  Maybe this function belongs elsewhere.
    """
    begin_time_unix = trip_struc["track_points"][0]["t"]
    zone_info = ZoneInfo(trip_struc.get("time_zone", "utc"))
    log.debug(f"Trip time zone: {zone_info}")
    for match in matches:
        if match["found"]:
            unix_time = match["time"]
            elapsed_seconds = unix_time - begin_time_unix
            elapsed = dt.timedelta(seconds=elapsed_seconds)
            passed = dt.datetime.fromtimestamp(unix_time, tz=zone_info)
            time_iso = passed.isoformat()
            time_local = passed.astimezone().strftime("%H:%M")
            match["time_iso"] = time_iso
            match["time_local"] = time_local
            match["time_elapsed"] = timedelta_hhmm(elapsed)  # Should display as HH:MM:SS
            km = match["dist"] / 1000.0
            match["dist_km"] = km
            match["dist_mi"] = km * 0.621371
        else:
            match["dist_km"] = -1
            match["dist_mi"] = -1
            match["time_iso"] = ""
            match["time_elapsed"] = ""
            match["time_local"] = ""

def timedelta_hhmm(td: dt.timedelta) -> str:
    """Convert a timedelta to a string of HH:MM (omitting seconds"""
    seconds = td.total_seconds()
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours:02d}:{minutes:02d}"




