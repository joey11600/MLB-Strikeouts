"""Game context store — schedule, lineups, venue, umpire, weather.

Pulls from MLB Stats API and Open-Meteo/NWS. Caches locally as JSON.

Key design decisions:
- Lineups stored with timestamps and source (projected vs confirmed)
- Weather is FORECAST, not observed (Gate 1)
- Umpire is the home plate umpire from boxscore.officials
- Venue includes roof type, coordinates, and field dimensions
"""
import json
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "game_context_cache"
MLB_BASE = "https://statsapi.mlb.com/api/v1"

VENUE_COORDS = {}


def _mlb_get(endpoint: str, params: dict | None = None) -> dict:
    """GET from MLB Stats API with rate limiting."""
    url = f"{MLB_BASE}/{endpoint}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "StrikeoutsModel/0.1")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_schedule(game_date: str) -> list[dict]:
    """Fetch MLB schedule for a date with probables, venue, and team info."""
    data = _mlb_get("schedule", {
        "sportId": "1",
        "date": game_date,
        "hydrate": "probablePitcher(note),team,venue,linescore,weather,lineups",
    })
    games = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            games.append(g)
    return games


def fetch_boxscore(game_pk: int) -> dict:
    """Fetch boxscore for a completed game (umpires, actual stats)."""
    return _mlb_get(f"game/{game_pk}/boxscore")


def fetch_venue_info(venue_id: int) -> dict:
    """Fetch venue details including roof type and coordinates."""
    data = _mlb_get("venues", {
        "venueIds": str(venue_id),
        "hydrate": "location,fieldInfo",
    })
    venues = data.get("venues", [])
    return venues[0] if venues else {}


def get_home_plate_umpire(boxscore: dict) -> dict | None:
    """Extract home plate umpire from boxscore officials."""
    for official in boxscore.get("officials", []):
        if official.get("officialType") == "Home Plate":
            return official.get("official", {})
    return None


def get_venue_coords(venue_id: int) -> tuple[float, float] | None:
    """Get (latitude, longitude) for a venue, with caching."""
    if venue_id in VENUE_COORDS:
        return VENUE_COORDS[venue_id]
    info = fetch_venue_info(venue_id)
    loc = info.get("location", {}).get("defaultCoordinates", {})
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if lat is not None and lon is not None:
        VENUE_COORDS[venue_id] = (lat, lon)
        return (lat, lon)
    return None


def fetch_weather_forecast(lat: float, lon: float, game_hour_utc: int = 23) -> dict:
    """Fetch weather forecast from Open-Meteo for a venue at game time.

    Uses FORECAST data, not observations. Gate 1 requires this.
    """
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&hourly=temperature_2m,relative_humidity_2m,dew_point_2m,"
        f"pressure_msl,surface_pressure,wind_speed_10m,"
        f"wind_direction_10m,wind_gusts_10m,cloud_cover"
        f"&temperature_unit=fahrenheit&wind_speed_unit=mph"
        f"&timezone=America/New_York&forecast_days=2"
    )
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "StrikeoutsModel/0.1")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


_COORDS_CACHE_PATH = Path(__file__).parent / "venue_coords.json"


def _load_coords_cache() -> None:
    """Warm VENUE_COORDS from disk so the worker doesn't re-ask the MLB
    API for the same 30 static ballpark locations every boot."""
    try:
        with open(_COORDS_CACHE_PATH, encoding="utf-8") as f:
            for k, v in json.load(f).items():
                VENUE_COORDS[int(k)] = tuple(v)
    except (OSError, ValueError):
        pass


def _save_coords_cache() -> None:
    try:
        with open(_COORDS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({str(k): list(v) for k, v in VENUE_COORDS.items()}, f)
    except OSError:
        pass


def game_weather(venue_id: int, start_time_utc: str) -> dict | None:
    """Compact pre-game weather forecast for one venue: the Open-Meteo
    hourly slot nearest first pitch (A-050 — the clients above sat
    unwired since Phase 0; no weather of any kind reached the system).

    FORECAST, not observation (Gate 1). Returns None on any failure —
    weather is a diagnostic capture today, and a missing value must
    stay missing (A-007), so callers store None rather than a default.
    """
    if not venue_id or not start_time_utc:
        return None
    try:
        if not VENUE_COORDS:
            _load_coords_cache()
        coords = get_venue_coords(venue_id)
        if coords is None:
            return None
        _save_coords_cache()
        raw = fetch_weather_forecast(*coords)
        hourly = raw.get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            return None
        # Open-Meteo returns local (America/New_York) hourly stamps;
        # convert first pitch to that clock and take the nearest slot.
        from zoneinfo import ZoneInfo
        ts = start_time_utc.strip().replace("Z", "+00:00")
        if "." in ts:
            # drop fractional seconds (".0000000") but keep the offset
            head, frac = ts.split(".", 1)
            ts = head + (frac[frac.index("+"):] if "+" in frac else "+00:00")
        start = datetime.fromisoformat(ts).astimezone(
            ZoneInfo("America/New_York"))
        target = start.strftime("%Y-%m-%dT%H:00")
        idx = times.index(target) if target in times else None
        if idx is None:
            return None
        def _at(key):
            vals = hourly.get(key, [])
            return vals[idx] if idx < len(vals) else None
        return {
            "temp_f": _at("temperature_2m"),
            "humidity_pct": _at("relative_humidity_2m"),
            "dew_point_f": _at("dew_point_2m"),
            "pressure_hpa": _at("pressure_msl"),
            "wind_mph": _at("wind_speed_10m"),
            "wind_dir_deg": _at("wind_direction_10m"),
            "gusts_mph": _at("wind_gusts_10m"),
            "cloud_pct": _at("cloud_cover"),
            "forecast_hour_local": target,
        }
    except Exception:
        return None


def fetch_nws_forecast(lat: float, lon: float) -> dict:
    """Fetch weather from NWS (license-clean fallback)."""
    url = f"https://api.weather.gov/points/{lat},{lon}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "(StrikeoutsModel, joey@jagsocialmedia.com)")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_lineup(game_data: dict, side: str) -> list[dict]:
    """Extract batting lineup from schedule data.

    side: 'home' or 'away'
    Returns list of dicts with player_id, name, batting_order.
    Array order IS batting order per MLB Stats API docs.
    """
    lineups = game_data.get("lineups", {})
    key = f"{side}Players" if f"{side}Players" in lineups else None
    if not key:
        return []
    players = lineups.get(key, [])
    result = []
    for i, p in enumerate(players):
        result.append({
            "player_id": p.get("id"),
            "name": p.get("fullName", ""),
            "batting_order": i + 1,
        })
    return result


def build_game_context(game_data: dict) -> dict:
    """Build full context dict for a single game from schedule data."""
    game_pk = game_data.get("gamePk")
    venue = game_data.get("venue", {})
    venue_id = venue.get("id")
    teams = game_data.get("teams", {})
    weather = game_data.get("weather", {})

    home_team = teams.get("home", {}).get("team", {})
    away_team = teams.get("away", {}).get("team", {})
    home_probable = teams.get("home", {}).get("probablePitcher", {})
    away_probable = teams.get("away", {}).get("probablePitcher", {})

    home_lineup = extract_lineup(game_data, "home")
    away_lineup = extract_lineup(game_data, "away")

    return {
        "game_pk": game_pk,
        "game_date": game_data.get("gameDate"),
        "game_type": game_data.get("gameType"),
        "status": game_data.get("status", {}).get("detailedState"),
        "venue_id": venue_id,
        "venue_name": venue.get("name"),
        "home_team_id": home_team.get("id"),
        "home_team_name": home_team.get("name"),
        "home_team_abbr": home_team.get("abbreviation"),
        "away_team_id": away_team.get("id"),
        "away_team_name": away_team.get("name"),
        "away_team_abbr": away_team.get("abbreviation"),
        "home_probable_id": home_probable.get("id"),
        "home_probable_name": home_probable.get("fullName"),
        "away_probable_id": away_probable.get("id"),
        "away_probable_name": away_probable.get("fullName"),
        # The beat-writer note the schedule hydration already requests
        # (probablePitcher(note)) and this dict silently dropped — it is
        # where announced pitch limits live ("expected to throw around
        # 75 pitches"), the exact information manual_pitch_limits.csv
        # has waited on since birth (A-024a / A-050).
        "home_probable_note": home_probable.get("note"),
        "away_probable_note": away_probable.get("note"),
        "home_lineup": home_lineup,
        "away_lineup": away_lineup,
        "lineup_source": "confirmed" if home_lineup else "none",
        "weather": weather,
    }


def cache_date(game_date: str) -> Path:
    """Cache game contexts for a date."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{game_date}.json"
    if cache_path.exists():
        return cache_path

    games = fetch_schedule(game_date)
    contexts = [build_game_context(g) for g in games]
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(contexts, f, indent=2)
    return cache_path


def load_date(game_date: str) -> list[dict]:
    """Load cached game contexts for a date."""
    cache_path = CACHE_DIR / f"{game_date}.json"
    if not cache_path.exists():
        cache_path = cache_date(game_date)
    with open(cache_path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    import sys
    game_date = sys.argv[1] if len(sys.argv) > 1 else "2026-08-04"
    print(f"Fetching game contexts for {game_date}...")
    contexts = load_date(game_date)
    print(f"Found {len(contexts)} games:")
    for ctx in contexts:
        hp = ctx["home_probable_name"] or "TBD"
        ap = ctx["away_probable_name"] or "TBD"
        lu = "lineups posted" if ctx["home_lineup"] else "no lineups yet"
        print(f"  {ctx['away_team_abbr']} @ {ctx['home_team_abbr']} — {ap} vs {hp} ({lu})")
