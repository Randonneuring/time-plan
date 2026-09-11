"""
Ride With GPS OAuth2 Sample
=========================
Initial version by Claude, now heavily modified but with the
OAuth2 flow intact.

This was a deliberately small Flask app that demonstrates the OAuth2
"Authorization Code" flow against the Ride With GPS (RWGPS) API.

WHAT IS OAUTH, IN ONE PARAGRAPH
--------------------------------
OAuth lets a user grant YOUR app limited access to THEIR data on another
service (here, RWGPS) without ever giving you their RWGPS password. The
flow has three parties: the USER (sitting in a browser), YOUR APP (this
Flask server), and the PROVIDER (RWGPS). Your app never sees the user's
credentials -- it only ever sees short-lived tokens that RWGPS hands out
after the user logs in directly with RWGPS.

THE FLOW THIS APP IMPLEMENTS (Authorization Code Grant)
---------------------------------------------------------
1. User clicks "Connect to Ride With GPS" in our app.
2. We redirect their browser to RWGPS's /oauth/authorize page, along with
   our client_id, a redirect_uri, and a random "state" value.
3. The user logs into RWGPS (on RWGPS's own site -- we never see the
   password) and clicks "Allow".
4. RWGPS redirects the browser BACK to us, at the redirect_uri we gave it,
   with a temporary "authorization code" in the query string.
5. Our server (not the browser) exchanges that code -- plus our client
   secret -- for an access_token by calling RWGPS's /oauth/token endpoint
   directly, server-to-server. This step requires the secret, which is
   why it must happen on the server, never in JavaScript in the browser.
6. We store the access_token (here, in the Flask session for simplicity)
   and use it on subsequent API calls, e.g. to fetch the user's rides.

WHY THE "STATE" PARAMETER MATTERS
----------------------------------
"state" is a random value we generate before step 2 and verify in step 4.
It defends against CSRF: without it, an attacker could trick a victim's
browser into completing an OAuth flow that's tied to the ATTACKER's
account, potentially confusing your app about which account is connected.

A NOTE ON ENDPOINTS
--------------------
Ride With GPS's exact OAuth URLs are issued/documented on your account's
"API clients" management page once you register an application there.
The defaults below follow the conventional Doorkeeper-style paths
(`/oauth/authorize`, `/oauth/token`) that RWGPS's dashboard describes, but
you should confirm them against your own client settings and adjust the
.env file if they differ.




"""

import os
import secrets
from urllib import response
from urllib.parse import urlencode

import flask

import route_trip_match

import requests
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for, jsonify
import datetime as dt

import logging
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# We load secrets from a .env file rather than hardcoding them, so that
# real credentials never end up committed to source control. See
# .env.example for the variables you need to fill in.
load_dotenv()

app = Flask(__name__)

# Force Flask to explicitly bound cookies to unencrypted local development addresses
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_DOMAIN'] = None  # Prevents locking to a specific host variant

# Flask uses this key to cryptographically sign the session cookie so users
# can't tamper with it. In a real app, set this to a long random value via
# an environment variable, never hardcode it.
log.debug(f"FLASK_SECRET_KEY: {os.environ['FLASK_SECRET_KEY']}")
app.secret_key = os.environ["FLASK_SECRET_KEY"]

RWGPS_CLIENT_ID = os.environ["RWGPS_CLIENT_ID"]
RWGPS_CLIENT_SECRET = os.environ["RWGPS_CLIENT_SECRET"]

# This must exactly match a redirect URI registered with your RWGPS API
# client. For local development this is typically http://127.0.0.1:5000/callback
RWGPS_REDIRECT_URI = os.environ["RWGPS_REDIRECT_URI"]

# Base URLs -- see the docstring above re: confirming these against your
# own account's API client page.
RWGPS_AUTHORIZE_URL = "https://ridewithgps.com/oauth/authorize"
RWGPS_TOKEN_URL = "https://ridewithgps.com/oauth/token"
RWGPS_REVOKE_URL = "https://ridewithgps.com/oauth/revoke"
RWGPS_API_BASE = "https://ridewithgps.com/api/v1"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


# ------------------------------
# Oauth protocol for logging in
# ------------------------------
@app.route("/")
def index():
    """
    Home page. Shows a "Connect" button if we don't yet have a token in the
    session, or a link to view rides if we do.
    """
    has_token = "access_token" in session
    return render_template("index.html", has_token=has_token)


@app.route("/login")
def login():
    """
    Step 2 of the flow: send the user's browser to RWGPS's authorization
    page. We don't make this request ourselves with `requests` -- we
    redirect the USER's browser there, because RWGPS needs to show the
    user a login form and a consent screen.
    """
    # Generate a fresh random value and stash it in the session so we can
    # compare it against what RWGPS sends back in /callback.
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    log.debug(f"Storing state in session: {state}")

    query_params = {
        "client_id": RWGPS_CLIENT_ID,
        "redirect_uri": RWGPS_REDIRECT_URI,
        "response_type": "code",  # "code" = Authorization Code grant
        "state": state,
        # Scopes define what we're asking permission for. Adjust this to
        # whatever scope names your RWGPS API client is configured with;
        # check your client's settings page for the exact scope strings
        # it supports.
        # "scope": "read",
    }
    log.debug(f"query_params: {query_params}")
    authorize_url = f"{RWGPS_AUTHORIZE_URL}?{urlencode(query_params)}"
    resp = redirect(authorize_url)
    log.debug(
        "Set-Cookie: %s",
        resp.headers.getlist("Set-Cookie")
    )
    log.debug(f"session.modified={session.modified}")
    log.debug(f"session={dict(session)}")
    return resp

@app.route("/rwgps_auth_callback")
def callback():
    """
    Step 4-5 of the flow: RWGPS redirects the browser back here after the
    user approves (or denies) access. We:
      1. Verify the 'state' parameter matches what we generated.
      2. Exchange the authorization 'code' for an access token by calling
         RWGPS's token endpoint directly (server-to-server, using
         `requests`, not the browser).
    """
    # --- Handle the user denying access, or any error RWGPS reports ---
    error = request.args.get("error")
    if error:
        return render_template("index.html", has_token=False,
                                error=f"Authorization failed: {error}")

    # --- CSRF check: does the returned state match what we sent? ---
    log.debug(f"Session at callback start: {dict(session)}")
    returned_state = request.args.get("state")
    expected_state = session.get("oauth_state", None)
    log.debug(f"Expected state: |{expected_state}| vs returned state |{returned_state}|")
    if not returned_state or returned_state != expected_state:
        return render_template("index.html", has_token=False,
                                error="State mismatch -- possible CSRF attempt. Please try again.")

    # --- Grab the authorization code RWGPS gave us ---
    code = request.args.get("code")
    if not code:
        return render_template("index.html", has_token=False,
                                error="No authorization code returned.")

    # --- Exchange the code for an access token ---
    # This is a normal server-to-server HTTP POST, made with the
    # `requests` library. It is NOT a browser redirect, because this step
    # requires our client_secret, which must stay confidential.
    token_response = requests.post(
        RWGPS_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": RWGPS_REDIRECT_URI,
            "client_id": RWGPS_CLIENT_ID,
            "client_secret": RWGPS_CLIENT_SECRET,
        },
        timeout=10,
    )

    if not token_response.ok:
        return render_template(
            "index.html", has_token=False,
            error=f"Token exchange failed ({token_response.status_code}): {token_response.text}",
        )

    token_data = token_response.json()

    # A typical OAuth2 token response looks like:
    # {
    #   "access_token": "...",
    #   "token_type": "bearer",
    #   "expires_in": 7200,
    #   "refresh_token": "...",   # may or may not be present
    #   "scope": "read"
    # }
    #
    # In a real production app you would persist this (associated with
    # your logged-in user) in a database, encrypted, and you'd handle
    # `expires_in` by refreshing the token before it expires using the
    # refresh_token. For this small demo, we keep it simple and just
    # store it in the Flask session.
    session["access_token"] = token_data["access_token"]
    session["refresh_token"] = token_data.get("refresh_token")
    return redirect(url_for("form_analyze_trip_get"))

# ------------------------------
#  The application proper, after OAuth authorization
# ------------------------------

"""
URL name scheme for form-handling: 
   /form_xxx_get  present the form   (GET only)
   /form_xxx_respond  handles the form submission  (POST only)
   templates/form_xxx.html the form
   
"""

#####
#  Analyzing a trip.
#  Form has entries for route URL, trip URL, and options like
#    whether to include directional cues or mile markers.
#####

@app.route("/form_analyze_trip_get", methods=["GET"])
def form_analyze_trip_get ():
    """After authorization, enter trip and route URLs and options for analysis"""
    return render_template("form_analyze_trip.html")

@app.route("/form_analyze_trip_respond", methods=["POST"])
def form_analyze_trip_respond():
    """We should get here from a form that provides URLs
    for the route and trip.   We need to extract the route and trip ID
    (integer strings) from the URLs.  We can't assume they are valid,
    even though the HTML form checks patterns, because a user could type
    correct-looking URLs into the form.
    """

    trip_url = request.form.get("trip_url", "")
    route_url = request.form.get("route_url", "")
    log.debug(f"trip_url: {trip_url}\nroute_url: {route_url}\n")

    trip_id = trip_url.split("/")[-1]
    # Could have ?privacy_code=... at end
    trip_id = trip_id.split("?")[0]
    assert trip_id.isdigit(), f"Don't hack me with a bad URL bro ({trip_url})"
    route_id = route_url.split("/")[-1]
    # Could have ?privacy_code=... at end
    route_id = route_id.split("?")[0]
    assert route_id.isdigit(), f"Don't hack me with a bad URL bro ({trip_url})"

    try:
        trip_struct = get_details(trip_id, "trip")
        log.debug(f"trip departed at: {trip_struct['departed_at']}")
        log.debug(f"trip time zone: {trip_struct['time_zone']}")
        route_struct = get_details(route_id, "route")
    except Exception as e:
        log.error(f"Error getting trip or route details: {e}")
        return redirect(url_for("index"))

    """Form data: 
    Check boxes: "cues", "miles_5", "miles_10", "km_10"
    Input fields (text):  route_url, trip_url
    """
    options = {}
    options["cues"] = request.form.get("cues", False)
    options["miles_5"] = request.form.get("miles_5", False)
    options["miles_10"] = request.form.get("miles_10", False    )
    options["km_10"] = request.form.get("km_10", False)
    # log.debug(f"Route points: {route_trip_match.route_points_from_rwgps(route_struct)}")
    landmarks = route_trip_match.route_points_from_rwgps(route_struct, options)
    #  Enrich landmarks according to options in the form:
    #   May include turn cues, mile/km markers
    trip_points = route_trip_match.trip_points_from_rwgps(trip_struct)

    # For debugging purposes, we want to turn the parallel arrays of trip
    locs, dists, times  = trip_points
    points = list(zip(locs, dists, times))

    matches = route_trip_match.matches(landmarks, trip_points)
    route_trip_match.humanize_matches_rwgps(matches, trip_struct)
    log.debug(f"matches: {matches}")

    return render_template("analysis.html",
                           trip=trip_struct, route=route_struct,
                           matches=matches,
                           error=None)

#####
#  Search for a trip.  This is a side-trip from the trip analysis
#     form, so that one can do the RWGPS search right in the app.
#  Two forms here:  one for the search, generating a list of candidate trips,
#     and one for selecting a trip from the list.
#  User can filter by name (partial match) and distance
#####

@app.route("/form_search_trips_get", methods=["GET"])
def form_search_trips_get():
    """User on trip form wants to search for a suitable trip"""
    return render_template("form_search_trips.html")


@app.route("/form_search_trips_respond", methods=["POST"])
def form_search_trips_respond():
    """User has filled the search form to find a trip."""
    trip_name = request.form.get("trip_name", "")
    min_km_field = request.form.get("min_km", "")
    max_km_field = request.form.get("max_km", "")
    log.debug(f"Search for trip_name: {trip_name} min_km: {min_km_field} max_km: {max_km_field}")
    if min_km_field.isdigit():
        min_km = int(min_km_field)
    else:
        min_km = 0
    if max_km_field.isdigit():
        max_km = int(max_km_field)
    else:
        max_km = 0  # Treat 0 as "any"
    trips = get_trips(trip_name, min_km, max_km)
    return render_template("form_select_trip.html", trips=trips)

@app.route("/form_select_trip_respond", methods=["POST"])
def form_select_trip_respond():
    """User has selected a trip from the list of candidate trips.
    Place it into session and redirect to trip analysis form.
    """
    trip_id = request.form.get("trip_id", "")
    if trip_id.isdigit():
        trip_url = f"https://ridewithgps.com/trips/{trip_id}"
        log.debug(f"trip: {trip_url}")
        session["trip_url"] = trip_url  # Accessible to form_analyze_trip
    return redirect(url_for("form_analyze_trip_get"))

#####
#  Search for a route.  This is a side-trip from the trip analysis
#     form, so that one can do the RWGPS search right in the app.
#  Two forms here:  one for the search, generating a list of candidate trips,
#     and one for selecting a trip from the list.
#  User can filter by name (partial match) and distance
#####

@app.route("/form_search_routes_get", methods=["GET"])
def form_search_routes_get():
    """User on trip form wants to search for a suitable trip"""
    return render_template("form_search_routes.html")


@app.route("/form_search_routes_respond", methods=["POST"])
def form_search_routes_respond():
    """User has filled the search form to find a trip."""
    route_name = request.form.get("route_name", "")
    min_km_field = request.form.get("min_km", "")
    max_km_field = request.form.get("max_km", "")
    log.debug(f"Search for route_name: {route_name} min_km: {min_km_field} max_km: {max_km_field}")
    if min_km_field.isdigit():
        min_km = int(min_km_field)
    else:
        min_km = 0
    if max_km_field.isdigit():
        max_km = int(max_km_field)
    else:
        max_km = 0  # Treat 0 as "any"
    routes = get_routes(route_name, min_km, max_km)
    return render_template("form_select_route.html", routes=routes)

@app.route("/form_select_route_respond", methods=["POST"])
def form_select_route_respond():
    """User has selected a route from the list of candidates.
    Place it into session and redirect to trip analysis form.
    """
    route_id = request.form.get("route_id", "")
    if route_id.isdigit():
        route_url = f"https://ridewithgps.com/routes/{route_id}"
        log.debug(f"route: {route_url}")
        session["route_url"] = route_url  # Accessible to form_analyze_trip
        session["route_id"] = route_id
    return redirect(url_for("form_analyze_trip_get"))


#####
# Logout discards session data
#####

@app.route("/logout")
def logout():
    """
    Clears our local session. Note: this does NOT revoke the token on
    RWGPS's side -- it just makes our app forget it. A thorough app would
    also call RWGPS's token revocation endpoint, if one is available.
    """
    access_token = session.get("access_token", None)
    if access_token:
        revocation_response = requests.post(
            RWGPS_REVOKE_URL,
            data={
                "token": access_token,
                "client_id": RWGPS_CLIENT_ID,
                "client_secret": RWGPS_CLIENT_SECRET,
            },
            timeout=10,
            )
        if revocation_response.ok:
            flask.flash("Logged out successfully.")
        else:
            log.error(f"Error revoking token: {revocation_response.text}")
            flask.flash(f"Error while trying to discard token: {revocation_response.text}")
    else:
        flask.flash("Already logged out.")

    session.clear()
    return redirect(url_for("index"))

# =================================
# Functions called from Jinja templates,
# especially for formatting
# ==================================
@app.template_filter("iso_to_date")
def iso_to_date(iso_date: str) -> str:
    """Convert ISO date string to human-readable date string"""
    return dt.datetime.fromisoformat(iso_date).strftime("%Y-%m-%d")

# =================================
# Unrouted functions (called by functions for templates).
# These are still "flask" functions because they depend
# on the Flask session object.
#
# In case of failure, these functions throw an exception
# that must be handled in the routed functions (e.g.,
# by redirecting to the index page with an error message)
# =================================

def get_trips(trip_name: str, min_km: str, max_km: str ) -> list[dict]:
    """Obtain list of trips from RWGPS API."""
    access_token = session.get("access_token")
    if not access_token:
        log.error("No access token in session")
        raise Exception("No access token in session")
    log.debug(f"Accessing trip list\naccess_token: {access_token} (valid)\n")

    # Authenticated API calls pass the access token in the Authorization
    # header, using the "Bearer" scheme -- this is standard OAuth2.
    headers = {"Authorization": f"Bearer {access_token}"}

    payload = {}
    if trip_name: payload["name"] = trip_name
    if min_km: payload["distance_min"] = min_km * 1000
    if max_km: payload["distance_max"] = max_km * 1000
    log.debug(f"Trip search payload: {payload}")

    response = requests.get(
        f"{RWGPS_API_BASE}/trips.json",
        headers=headers, params=payload,
        timeout=10,
    )

    if response.status_code == 401:
        # The token expired or was revoked. A production app would try to
        # use the refresh_token here to get a new access_token. For this
        # demo, we just send the user back to log in again.
        log.error(f"Token expired or revoked\n{response.text}")
        session.pop("access_token", None)
        session.pop("refresh_token", None)
        flask.flash("Your session has expired. Please log in again.")
        raise Exception("Token expired or revoked")

    if not response.ok:
        flask.flash(f"Error fetching trip list: {response.text}")
        raise Exception(f"RWGPS API error ({response.status_code}): {response.text}")

    data = response.json()
    trips_list = sorted(data["trips"], key=lambda trip: trip["departed_at"], reverse=True)
    return trips_list


def get_routes(route_name: str, min_km: str, max_km: str ) -> list[dict]:
    """Obtain list of routes from RWGPS API."""
    access_token = session.get("access_token")
    if not access_token:
        log.error("No access token in session")
        raise Exception("No access token in session")
    log.debug(f"Accessing route list\naccess_token: {access_token} (valid)\n")

    # Authenticated API calls pass the access token in the Authorization
    # header, using the "Bearer" scheme -- this is standard OAuth2.
    headers = {"Authorization": f"Bearer {access_token}"}

    payload = {}
    if route_name: payload["name"] = route_name
    if min_km: payload["distance_min"] = min_km * 1000
    if max_km: payload["distance_max"] = max_km * 1000
    log.debug(f"Route search payload: {payload}")

    response = requests.get(
        f"{RWGPS_API_BASE}/routes.json",
        headers=headers, params=payload,
        timeout=10,
    )

    if response.status_code == 401:
        # The token expired or was revoked. A production app would try to
        # use the refresh_token here to get a new access_token. For this
        # demo, we just send the user back to log in again.
        log.error(f"Token expired or revoked\n{response.text}")
        session.pop("access_token", None)
        session.pop("refresh_token", None)
        flask.flash("Your session has expired. Please log in again.")
        raise Exception("Token expired or revoked")

    if not response.ok:
        flask.flash(f"Error fetching route list: {response.text}")
        raise Exception(f"RWGPS API error ({response.status_code}): {response.text}")

    data = response.json()
    routes_list = sorted(data["routes"], key=lambda route: route["distance"])
    return routes_list


def get_details(item_id: str, item_kind: str):
    """Obtains the details for a trip (item_kind="trip")
    or route (item_kind="route") from RWGPS API.
    Extracts and returns the details as a dictionary.
    """
    access_token = session.get("access_token")
    if not access_token:
        log.error("No access token in session")
        raise Exception("No access token in session")
    log.debug(f"Accessing:{item_kind} {item_id}\naccess_token: {access_token} (valid)\n")

    # Authenticated API calls pass the access token in the Authorization
    # header, using the "Bearer" scheme -- this is standard OAuth2.
    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(
        f"{RWGPS_API_BASE}/{item_kind}s/{item_id}.json",
        headers=headers,
        timeout=10,
    )

    if response.status_code == 401:
        # The token expired or was revoked. A production app would try to
        # use the refresh_token here to get a new access_token. For this
        # demo, we just send the user back to log in again.
        log.error(f"Token expired or revoked\n{response.text}")
        session.pop("access_token", None)
        session.pop("refresh_token", None)
        flask.flash("Your session has expired. Please log in again.")
        raise Exception("Token expired or revoked")

    if not response.ok:
        flask.flash(f"Error fetching {item_kind} details: {response.text}")
        raise Exception(f"RWGPS API error ({response.status_code}): {response.text}")

    data = response.json()
    item_struct = data.get(item_kind, {})
    return item_struct


if __name__ == "__main__":
    # debug=True gives you helpful error pages during development.
    # Never run with debug=True in production.
    app.run(debug=True, port=5475)
else:
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(logging.DEBUG)  # Explicitly allow DEBUG logs
