import time
from math import radians, sin, cos, asin, sqrt

from flask import g

from ruqqus.classes.paypal import PayPalTxn
from ruqqus.classes.login_events import LoginEvent

# Cloudflare's special pseudo-country code for Tor exit-node traffic.
# Already used ad-hoc as a literal elsewhere (ruqqus/classes/user.py,
# ruqqus/routes/admin.py) - kept here too since this module owns region/VPN logic.
TOR_COUNTRY_CODE = "T1"

# How far back to pull a user's login history when recomputing their region/
# suspicion state. Comfortably covers the stability windows below with margin,
# while bounding the query/row count per user per recompute pass.
LOOKBACK_DAYS = 400

# A user's displayed region only migrates once a single different region has
# been their consistent login location for this long.
STABILITY_WINDOW_DAYS = 180

# Halved stability window used when the user currently has active premium AND
# their most recent PayPal payer-country capture matches the candidate region.
FAST_TRACK_WINDOW_DAYS = STABILITY_WINDOW_DAYS // 2

# Chosen comfortably above commercial flight speed (~900 km/h) so genuine air
# travel between two real logins never trips the implausible-travel check.
MAX_PLAUSIBLE_SPEED_KMH = 1000

# Approximate centroid (lat, lon) per region, used only for coarse distance
# checks (impossible-travel detection) - intentionally not precise, since the
# whole point of a region is to not pin down anyone's real location.
REGION_CENTROIDS = {
    "eastern_europe_russia": (55.75, 37.62),
    "central_asia": (43.2, 76.9),
    "iran_afghanistan": (35.7, 51.4),
    "east_asia": (39.9, 116.4),
    "maritime_southeast_asia": (-6.2, 106.8),
    "mainland_southeast_asia": (13.75, 100.5),
    "south_asia": (28.6, 77.2),
    "arabian_peninsula": (24.7, 46.7),
    "caucasus_turkiye": (40.0, 38.0),
    "oceania": (-25.0, 135.0),
    "north_africa": (30.0, 31.2),
    "balkans": (44.8, 20.5),
    "central_europe": (52.2, 21.0),
    "finland_baltics": (60.2, 24.9),
    "scandinavia": (59.3, 18.1),
    "british_isles": (51.5, -0.1),
    "southwestern_europe": (40.4, -3.7),
    "west_africa": (6.5, 3.4),
    "sahel": (12.6, -8.0),
    "central_southeastern_africa": (-4.3, 15.3),
    "southern_africa": (-26.2, 28.0),
    "horn_of_africa": (9.0, 38.7),
    "north_america": (39.8, -98.5),
    "caribbean": (23.1, -82.4),
    "central_america": (19.4, -99.1),
    "andean_north": (-12.0, -77.0),
    "southern_cone": (-34.6, -58.4),
    "northern_south_america": (4.7, -74.1),
    "levant_mesopotamia": (33.3, 44.4),
    "western_central_europe": (52.5, 13.4),
    "danubian_steppe": (46.5, 23.5),
}

# Static ISO 3166-1 alpha-2 -> region-code lookup, derived 1:1 from the
# MapChart groupings (mapchartSave__world__.txt). Do not hand-edit without
# updating that source file's grouping too.
COUNTRY_TO_REGION = {
    "AD": "southwestern_europe", "AE": "arabian_peninsula", "AF": "iran_afghanistan",
    "AG": "caribbean", "AI": "caribbean", "AL": "balkans", "AM": "caucasus_turkiye",
    "AO": "central_southeastern_africa", "AR": "southern_cone", "AS": "oceania",
    "AT": "western_central_europe", "AU": "oceania", "AW": "caribbean", "AX": "finland_baltics",
    "AZ": "caucasus_turkiye", "BA": "balkans", "BB": "caribbean", "BD": "south_asia",
    "BE": "western_central_europe", "BF": "west_africa", "BG": "balkans",
    "BH": "arabian_peninsula", "BI": "central_southeastern_africa", "BJ": "west_africa",
    "BL": "caribbean", "BM": "north_america", "BN": "maritime_southeast_asia",
    "BO": "andean_north", "BQ": "caribbean", "BR": "northern_south_america", "BS": "caribbean",
    "BT": "mainland_southeast_asia", "BW": "southern_africa", "BY": "eastern_europe_russia",
    "BZ": "central_america", "CA": "north_america", "CD": "central_southeastern_africa",
    "CF": "west_africa", "CG": "central_southeastern_africa", "CH": "western_central_europe",
    "CI": "west_africa", "CK": "oceania", "CL": "southern_cone", "CM": "west_africa",
    "CN": "east_asia", "CO": "northern_south_america", "CR": "central_america", "CU": "caribbean",
    "CV": "west_africa", "CW": "caribbean", "CY": "levant_mesopotamia", "CZ": "central_europe",
    "DE": "western_central_europe", "DJ": "horn_of_africa", "DK": "scandinavia", "DM": "caribbean",
    "DO": "caribbean", "DZ": "north_africa", "EC": "andean_north", "EE": "finland_baltics",
    "EG": "north_africa", "EH": "north_africa", "ER": "horn_of_africa",
    "ES": "southwestern_europe", "ET": "horn_of_africa", "FI": "finland_baltics", "FJ": "oceania",
    "FK": "southern_cone", "FM": "oceania", "FO": "british_isles", "FR": "southwestern_europe",
    "GA": "central_southeastern_africa", "GB": "british_isles", "GD": "caribbean",
    "GE": "caucasus_turkiye", "GF": "northern_south_america", "GG": "british_isles",
    "GH": "west_africa", "GI": "southwestern_europe", "GL": "north_america", "GM": "west_africa",
    "GN": "west_africa", "GP": "caribbean", "GQ": "central_southeastern_africa", "GR": "balkans",
    "GT": "central_america", "GU": "oceania", "GW": "west_africa", "GY": "northern_south_america",
    "HK": "east_asia", "HN": "central_america", "HR": "balkans", "HT": "caribbean",
    "HU": "danubian_steppe", "ID": "maritime_southeast_asia", "IE": "british_isles",
    "IL": "levant_mesopotamia", "IM": "british_isles", "IN": "south_asia",
    "IQ": "levant_mesopotamia", "IR": "iran_afghanistan", "IS": "british_isles",
    "IT": "southwestern_europe", "JE": "british_isles", "JM": "caribbean",
    "JO": "levant_mesopotamia", "JP": "east_asia", "KE": "horn_of_africa", "KG": "central_asia",
    "KH": "mainland_southeast_asia", "KI": "oceania", "KM": "central_southeastern_africa",
    "KN": "caribbean", "KP": "east_asia", "KR": "east_asia", "KW": "arabian_peninsula",
    "KY": "caribbean", "KZ": "central_asia", "LA": "mainland_southeast_asia",
    "LB": "levant_mesopotamia", "LC": "caribbean", "LI": "western_central_europe",
    "LK": "south_asia", "LR": "west_africa", "LS": "southern_africa", "LT": "finland_baltics",
    "LU": "western_central_europe", "LV": "finland_baltics", "LY": "north_africa",
    "MA": "north_africa", "MC": "southwestern_europe", "MD": "danubian_steppe", "ME": "balkans",
    "MF": "caribbean", "MG": "central_southeastern_africa", "MH": "oceania", "MK": "balkans",
    "ML": "sahel", "MM": "mainland_southeast_asia", "MN": "central_asia", "MO": "east_asia",
    "MP": "oceania", "MQ": "caribbean", "MR": "sahel", "MS": "caribbean",
    "MT": "southwestern_europe", "MU": "central_southeastern_africa", "MV": "south_asia",
    "MW": "central_southeastern_africa", "MX": "central_america", "MY": "maritime_southeast_asia",
    "MZ": "central_southeastern_africa", "NA": "southern_africa", "NC": "oceania", "NE": "sahel",
    "NG": "west_africa", "NI": "central_america", "NL": "western_central_europe",
    "NO": "scandinavia", "NP": "south_asia", "NR": "oceania", "NU": "oceania", "NZ": "oceania",
    "OM": "arabian_peninsula", "PA": "central_america", "PE": "andean_north", "PF": "oceania",
    "PG": "oceania", "PH": "maritime_southeast_asia", "PK": "south_asia", "PL": "central_europe",
    "PM": "north_america", "PR": "caribbean", "PS": "levant_mesopotamia",
    "PT": "southwestern_europe", "PW": "oceania", "PY": "southern_cone", "QA": "arabian_peninsula",
    "RE": "central_southeastern_africa", "RO": "danubian_steppe", "RS": "balkans",
    "RU": "eastern_europe_russia", "RW": "central_southeastern_africa", "SA": "arabian_peninsula",
    "SB": "oceania", "SC": "central_southeastern_africa", "SD": "horn_of_africa",
    "SE": "scandinavia", "SG": "maritime_southeast_asia", "SI": "balkans", "SK": "central_europe",
    "SL": "west_africa", "SM": "southwestern_europe", "SN": "west_africa", "SO": "horn_of_africa",
    "SR": "northern_south_america", "SS": "horn_of_africa", "ST": "central_southeastern_africa",
    "SV": "central_america", "SX": "caribbean", "SY": "levant_mesopotamia",
    "SZ": "southern_africa", "TC": "caribbean", "TD": "horn_of_africa", "TG": "west_africa",
    "TH": "mainland_southeast_asia", "TJ": "iran_afghanistan", "TL": "maritime_southeast_asia",
    "TM": "central_asia", "TN": "north_africa", "TO": "oceania", "TR": "caucasus_turkiye",
    "TT": "caribbean", "TV": "oceania", "TW": "east_asia", "TZ": "central_southeastern_africa",
    "UA": "eastern_europe_russia", "UG": "horn_of_africa", "US": "north_america",
    "UY": "southern_cone", "UZ": "central_asia", "VA": "southwestern_europe", "VC": "caribbean",
    "VE": "northern_south_america", "VG": "caribbean", "VI": "caribbean",
    "VN": "mainland_southeast_asia", "VU": "oceania", "WF": "oceania", "WS": "oceania",
    "XK": "balkans", "YE": "arabian_peninsula", "YT": "central_southeastern_africa",
    "ZA": "southern_africa", "ZM": "central_southeastern_africa",
    "ZW": "central_southeastern_africa",
}

# Display-only: ISO alpha-2 -> human-readable name, generated 1:1 from the
# MapChart export's labels. Not used for any geo-resolution logic - purely for
# rendering a readable country list on the /regions page.
COUNTRY_NAMES = {
    "AD": "Andorra", "AE": "United Arab Emirates", "AF": "Afghanistan",
    "AG": "Antigua and Barbuda", "AI": "Anguilla", "AL": "Albania", "AM": "Armenia",
    "AO": "Angola", "AR": "Argentina", "AS": "American Samoa", "AT": "Austria", "AU": "Australia",
    "AW": "Aruba", "AX": "Åland Islands", "AZ": "Azerbaijan", "BA": "Bosnia and Herzegovina",
    "BB": "Barbados", "BD": "Bangladesh", "BE": "Belgium", "BF": "Burkina Faso", "BG": "Bulgaria",
    "BH": "Bahrain", "BI": "Burundi", "BJ": "Benin", "BL": "Saint Barthélemy", "BM": "Bermuda",
    "BN": "Brunei", "BO": "Bolivia", "BQ": "Caribbean Netherlands", "BR": "Brazil",
    "BS": "Bahamas", "BT": "Bhutan", "BW": "Botswana", "BY": "Belarus", "BZ": "Belize",
    "CA": "Canada", "CD": "DR Congo", "CF": "Central African Republic", "CG": "Congo",
    "CH": "Switzerland", "CI": "Côte d'Ivoire", "CK": "Cook Islands", "CL": "Chile",
    "CM": "Cameroon", "CN": "China", "CO": "Colombia", "CR": "Costa Rica", "CU": "Cuba",
    "CV": "Cabo Verde", "CW": "Curaçao", "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany",
    "DJ": "Djibouti", "DK": "Denmark", "DM": "Dominica", "DO": "Dominican Republic",
    "DZ": "Algeria", "EC": "Ecuador", "EE": "Estonia", "EG": "Egypt", "EH": "Western Sahara",
    "ER": "Eritrea", "ES": "Spain", "ET": "Ethiopia", "FI": "Finland", "FJ": "Fiji",
    "FK": "Falkland Islands", "FM": "Micronesia", "FO": "Faeroe Islands", "FR": "France",
    "GA": "Gabon", "GB": "United Kingdom", "GD": "Grenada", "GE": "Georgia", "GF": "French Guiana",
    "GG": "Guernsey", "GH": "Ghana", "GI": "Gibraltar", "GL": "Greenland", "GM": "Gambia",
    "GN": "Guinea", "GP": "Guadeloupe", "GQ": "Equatorial Guinea", "GR": "Greece",
    "GT": "Guatemala", "GU": "Guam", "GW": "Guinea Bissau", "GY": "Guyana", "HK": "Hong Kong",
    "HN": "Honduras", "HR": "Croatia", "HT": "Haiti", "HU": "Hungary", "ID": "Indonesia",
    "IE": "Ireland", "IL": "Israel", "IM": "Isle of Man", "IN": "India", "IQ": "Iraq",
    "IR": "Iran", "IS": "Iceland", "IT": "Italy", "JE": "Jersey", "JM": "Jamaica", "JO": "Jordan",
    "JP": "Japan", "KE": "Kenya", "KG": "Kyrgyzstan", "KH": "Cambodia", "KI": "Kiribati",
    "KM": "Comoros", "KN": "Saint Kitts and Nevis", "KP": "North Korea", "KR": "South Korea",
    "KW": "Kuwait", "KY": "Cayman Islands", "KZ": "Kazakhstan", "LA": "Laos", "LB": "Lebanon",
    "LC": "Saint Lucia", "LI": "Liechtenstein", "LK": "Sri Lanka", "LR": "Liberia",
    "LS": "Lesotho", "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia", "LY": "Libya",
    "MA": "Morocco", "MC": "Monaco", "MD": "Moldova", "ME": "Montenegro", "MF": "Saint Martin",
    "MG": "Madagascar", "MH": "Marshall Islands", "MK": "North Macedonia", "ML": "Mali",
    "MM": "Myanmar", "MN": "Mongolia", "MO": "Macau", "MP": "Northern Mariana Islands",
    "MQ": "Martinique", "MR": "Mauritania", "MS": "Montserrat", "MT": "Malta", "MU": "Mauritius",
    "MV": "Maldives", "MW": "Malawi", "MX": "Mexico", "MY": "Malaysia", "MZ": "Mozambique",
    "NA": "Namibia", "NC": "New Caledonia", "NE": "Niger", "NG": "Nigeria", "NI": "Nicaragua",
    "NL": "Netherlands", "NO": "Norway", "NP": "Nepal", "NR": "Nauru", "NU": "Niue",
    "NZ": "New Zealand", "OM": "Oman", "PA": "Panama", "PE": "Peru", "PF": "French Polynesia",
    "PG": "Papua New Guinea", "PH": "Philippines", "PK": "Pakistan", "PL": "Poland",
    "PM": "Saint Pierre and Miquelon", "PR": "Puerto Rico", "PS": "Palestinian Territories",
    "PT": "Portugal", "PW": "Palau", "PY": "Paraguay", "QA": "Qatar", "RE": "Réunion",
    "RO": "Romania", "RS": "Serbia", "RU": "Russia", "RW": "Rwanda", "SA": "Saudi Arabia",
    "SB": "Solomon Islands", "SC": "Seychelles", "SD": "Sudan", "SE": "Sweden", "SG": "Singapore",
    "SI": "Slovenia", "SK": "Slovakia", "SL": "Sierra Leone", "SM": "San Marino", "SN": "Senegal",
    "SO": "Somalia", "SR": "Suriname", "SS": "South Sudan", "ST": "São Tomé and Príncipe",
    "SV": "El Salvador", "SX": "Sint Maarten", "SY": "Syria", "SZ": "Eswatini",
    "TC": "Turks and Caicos Islands", "TD": "Chad", "TG": "Togo", "TH": "Thailand",
    "TJ": "Tajikistan", "TL": "Timor-Leste", "TM": "Turkmenistan", "TN": "Tunisia", "TO": "Tonga",
    "TR": "Türkiye", "TT": "Trinidad and Tobago", "TV": "Tuvalu", "TW": "Taiwan", "TZ": "Tanzania",
    "UA": "Ukraine", "UG": "Uganda", "US": "United States", "UY": "Uruguay", "UZ": "Uzbekistan",
    "VA": "Vatican City", "VC": "Saint Vincent and the Grenadines", "VE": "Venezuela",
    "VG": "British Virgin Islands", "VI": "United States Virgin Islands", "VN": "Vietnam",
    "VU": "Vanuatu", "WF": "Wallis and Futuna", "WS": "Samoa", "XK": "Kosovo", "YE": "Yemen",
    "YT": "Mayotte", "ZA": "South Africa", "ZM": "Zambia", "ZW": "Zimbabwe",
}


# Reverse of COUNTRY_TO_REGION: region code -> sorted list of its country codes.
# Used by the /regions map page to colour each country by its region.
REGION_COUNTRIES = {}
for _cc, _region_code in COUNTRY_TO_REGION.items():
    REGION_COUNTRIES.setdefault(_region_code, []).append(_cc)
for _countries in REGION_COUNTRIES.values():
    _countries.sort()
del _cc, _region_code, _countries


def is_tor(cf_country):
    return cf_country == TOR_COUNTRY_CODE


def resolve_region(cf_country):
    """Map a raw cf-ipcountry value to a region code.

    Returns None for Tor traffic or any code we don't recognize (Cloudflare
    can emit non-ISO pseudo-codes like "XX" or "EU" - fail soft, never raise).
    """
    if not cf_country or is_tor(cf_country):
        return None

    return COUNTRY_TO_REGION.get(cf_country.upper())


def _km_between(coord_a, coord_b):
    """Great-circle distance in km between two (lat, lon) pairs."""

    lat1, lon1 = radians(coord_a[0]), radians(coord_a[1])
    lat2, lon2 = radians(coord_b[0]), radians(coord_b[1])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def is_physically_implausible(event_a, event_b):
    """Given two LoginEvents (any order), decide if the region change between
    them could not physically have happened in the elapsed time."""

    if not event_a.region_code or not event_b.region_code:
        return False

    if event_a.region_code == event_b.region_code:
        return False

    coord_a = REGION_CENTROIDS.get(event_a.region_code)
    coord_b = REGION_CENTROIDS.get(event_b.region_code)

    if not coord_a or not coord_b:
        return False

    distance_km = _km_between(coord_a, coord_b)
    elapsed_hours = abs(event_b.created_utc - event_a.created_utc) / 3600

    if elapsed_hours <= 0:
        return distance_km > 0

    return (distance_km / elapsed_hours) > MAX_PLAUSIBLE_SPEED_KMH


def _longest_streak_span(resolved_events, now):
    """Among time-ordered resolved_events (Tor/unresolved already excluded),
    find the longest run of consecutive same-region events, and the "current"
    (most recent) run. Returns (longest_span_seconds, current_region, current_span_seconds)."""

    if not resolved_events:
        return 0, None, 0

    longest_span = 0
    run_start = resolved_events[0].created_utc
    run_region = resolved_events[0].region_code

    for prev, cur in zip(resolved_events, resolved_events[1:]):
        if cur.region_code != run_region:
            span = prev.created_utc - run_start
            longest_span = max(longest_span, span)
            run_start = cur.created_utc
            run_region = cur.region_code

    # close out the final run, anchored to now since it's still ongoing
    current_region = run_region
    current_span = now - run_start
    longest_span = max(longest_span, current_span)

    return longest_span, current_region, current_span


def compute_region_state(user, db=None):
    """Pure function: reads user's LoginEvent history and decides whether their
    displayed region should migrate and/or be flagged as suspicious.

    Returns {"suspicious": bool, "migrate_to": str|None}. Does not write to the
    DB - the caller (the recompute job) applies the result.

    `db` defaults to Flask's request-scoped g.db for callers inside a request
    (e.g. an admin/debug route); the standalone recompute job passes its own
    plain session explicitly since it has no Flask request context.
    """

    if db is None:
        db = g.db

    now = int(time.time())
    cutoff = now - LOOKBACK_DAYS * 86400

    events = (
        db.query(LoginEvent)
        .filter(LoginEvent.user_id == user.id, LoginEvent.created_utc >= cutoff)
        .order_by(LoginEvent.created_utc.asc())
        .all()
    )

    if not events:
        return {"suspicious": False, "migrate_to": None}

    has_tor = any(is_tor(e.cf_country) for e in events)
    resolved_events = [e for e in events if e.region_code and not is_tor(e.cf_country)]

    implausible = any(
        is_physically_implausible(a, b)
        for a, b in zip(resolved_events, resolved_events[1:])
    )

    if user.has_premium_no_renew:
        last_txn = (
            db.query(PayPalTxn)
            .filter(PayPalTxn.user_id == user.id, PayPalTxn.payer_country.isnot(None))
            .order_by(PayPalTxn.created_utc.desc())
            .first()
        )
        payer_region = resolve_region(last_txn.payer_country) if last_txn else None
    else:
        payer_region = None

    longest_span, current_region, current_span = _longest_streak_span(resolved_events, now)

    has_full_window_of_history = (now - resolved_events[0].created_utc) >= STABILITY_WINDOW_DAYS * 86400 if resolved_events else False

    required_seconds = STABILITY_WINDOW_DAYS * 86400
    if payer_region and payer_region == current_region:
        required_seconds = FAST_TRACK_WINDOW_DAYS * 86400

    migrate_to = None
    if current_region and current_region != user.display_region and current_span >= required_seconds:
        migrate_to = current_region

    # Perpetual-nomad: we've watched this user for a full stability window and
    # no single region (including their current one) ever held for that long.
    nomad = has_full_window_of_history and longest_span < STABILITY_WINDOW_DAYS * 86400

    return {
        "suspicious": has_tor or implausible or nomad,
        "migrate_to": migrate_to,
    }
