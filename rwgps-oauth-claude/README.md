# Ride With GPS OAuth2 Demo

A minimal Flask app that demonstrates logging a user into Ride With GPS
(RWGPS) via OAuth2 and then fetching their ride data. Built as a learning
example — every step of the OAuth dance is commented in `app.py`.

## What you'll learn from this code

- The OAuth2 "Authorization Code" grant flow (the standard, secure flow
  for server-side web apps)
- Why the token exchange happens server-to-server, not in the browser
- What the `state` parameter protects against, and why it matters
- How to call an API on a user's behalf using a Bearer token
- Basic session handling in Flask

## 1. Register an API client with Ride With GPS

1. Log into your account at ridewithgps.com.
2. Go to **Account Settings → Developers** and create an API client.
3. On that client's settings page, configure OAuth and add a redirect
   URI of `http://127.0.0.1:5000/callback` (for local testing).
4. Note your **Client ID** and **Client Secret**.
5. **Important:** RWGPS's exact OAuth endpoint paths (authorize/token
   URLs) and supported scope names are documented on that same client
   settings page. This demo assumes the conventional paths
   `https://ridewithgps.com/oauth/authorize` and
   `https://ridewithgps.com/oauth/token`, and a scope of `read` — double
   check these against your client's page and adjust the constants near
   the top of `app.py` if they differ.

## 2. Set up the project

```bash
cd rwgps_oauth_demo
python -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# now edit .env and fill in FLASK_SECRET_KEY, RWGPS_CLIENT_ID,
# RWGPS_CLIENT_SECRET, RWGPS_REDIRECT_URI
```

## 3. Run it

```bash
python app.py
```

Visit `http://127.0.0.1:5000` in your browser, click **Connect to Ride
With GPS**, log in and approve access, and you should land on a page
listing your rides.

## How the pieces fit together

```
 Browser                  Your Flask App                RWGPS
   |                            |                          |
   |--- GET / -----------------> |                          |
   |<---- "Connect" button ------|                          |
   |                            |                          |
   |--- click "Connect" -------> |  /login                 |
   |<---- redirect to RWGPS -----|                          |
   |                                                        |
   |---------- GET /oauth/authorize?client_id=... --------->|
   |                              (user logs in & approves)  |
   |<----------------- redirect with ?code=...&state=... ----|
   |                            |                          |
   |--- GET /callback?code=... -> |                          |
   |                            |--- POST /oauth/token ----->|
   |                            |    (code + client_secret)  |
   |                            |<--- access_token ----------|
   |<---- redirect to /rides ----|                          |
   |                            |--- GET /api/v1/trips.json->|
   |                            |    (Authorization: Bearer) |
   |                            |<--- ride data --------------|
   |<---- rendered rides page ---|                          |
```

The key security idea: the browser only ever talks to RWGPS for the
login/consent screen. The actual exchange of a code for a token, which
requires your secret `client_secret`, happens directly between your
server and RWGPS's server, where the secret can't be seen by the user or
intercepted by JavaScript running in their browser.

## Notes for going further

This demo intentionally keeps things simple by storing the token in the
Flask session (an encrypted cookie). A production app would instead:

- Store tokens in a database, associated with your own user accounts
- Encrypt tokens at rest
- Use the `refresh_token` to silently renew an expired `access_token`
  instead of forcing the user to log in again
- Handle token revocation if a user disconnects the integration