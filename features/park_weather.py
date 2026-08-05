"""Group E — Park, environment, and weather features.

T1: E1 (park K factor, residualized), E2 (park FB-whiff factor),
    E3 (altitude/air density), E4 (altitude x arsenal), E5 (roof),
    E6 (temperature x pitch mix), E17 (air density, computed).

E17 is the single physical variable. E1 carries only the residual.
Train on FORECAST weather, not ERA5 observations.
"""
import json
import math
import sys
from pathlib import Path

VENUE_DATA = {
    2392: {"name": "Busch Stadium", "alt_ft": 465, "roof": "open"},
    2394: {"name": "Fenway Park", "alt_ft": 20, "roof": "open"},
    2395: {"name": "Globe Life Field", "alt_ft": 525, "roof": "retractable"},
    2602: {"name": "Kauffman Stadium", "alt_ft": 750, "roof": "open"},
    2680: {"name": "T-Mobile Park", "alt_ft": 0, "roof": "retractable"},
    3289: {"name": "Wrigley Field", "alt_ft": 595, "roof": "open"},
    3309: {"name": "Angel Stadium", "alt_ft": 160, "roof": "open"},
    3312: {"name": "Dodger Stadium", "alt_ft": 515, "roof": "open"},
    3313: {"name": "Yankee Stadium", "alt_ft": 55, "roof": "open"},
    4705: {"name": "Oracle Park", "alt_ft": 0, "roof": "open"},
    15: {"name": "Chase Field", "alt_ft": 1059, "roof": "retractable"},
    2536: {"name": "PNC Park", "alt_ft": 730, "roof": "open"},
    2889: {"name": "Tropicana Field", "alt_ft": 0, "roof": "dome"},
    5325: {"name": "Petco Park", "alt_ft": 13, "roof": "open"},
    2392: {"name": "Busch Stadium", "alt_ft": 465, "roof": "open"},
    2532: {"name": "Minute Maid Park", "alt_ft": 42, "roof": "retractable"},
    19: {"name": "Coors Field", "alt_ft": 5280, "roof": "open"},
    2: {"name": "Citizens Bank Park", "alt_ft": 20, "roof": "open"},
    31: {"name": "Great American Ball Park", "alt_ft": 490, "roof": "open"},
    12: {"name": "Target Field", "alt_ft": 815, "roof": "open"},
    17: {"name": "Comerica Park", "alt_ft": 600, "roof": "open"},
    680: {"name": "Guaranteed Rate Field", "alt_ft": 595, "roof": "open"},
    3: {"name": "Progressive Field", "alt_ft": 660, "roof": "open"},
    10: {"name": "Camden Yards", "alt_ft": 30, "roof": "open"},
    14: {"name": "Nationals Park", "alt_ft": 20, "roof": "open"},
    4169: {"name": "American Family Field", "alt_ft": 635, "roof": "retractable"},
    22: {"name": "loanDepot park", "alt_ft": 10, "roof": "retractable"},
    4249: {"name": "Truist Park", "alt_ft": 1050, "roof": "open"},
    3714: {"name": "Citi Field", "alt_ft": 14, "roof": "open"},
    3321: {"name": "Oakland Coliseum", "alt_ft": 0, "roof": "open"},
    2681: {"name": "Rogers Centre", "alt_ft": 250, "roof": "retractable"},
}

PARK_K_FACTORS = {
    19: 0.88,     # Coors — low K
    2889: 1.12,   # Tropicana — high K (dome, lighting)
    4705: 1.06,   # Oracle — marine air
    2394: 0.96,   # Fenway
    3313: 1.02,   # Yankee Stadium
}

PARK_FB_WHIFF = {
    2889: 0.240,  # Tropicana
    2680: 0.230,  # T-Mobile (dome)
    4705: 0.225,  # Oracle
    19: 0.165,    # Coors
}

LEAGUE_FB_WHIFF = 0.205


def _air_density(altitude_ft: float, temp_f: float = 72.0,
                  humidity_pct: float = 50.0,
                  pressure_hpa: float | None = None) -> float:
    """E17: compute air density in kg/m³.

    Uses the barometric formula adjusted for temperature and humidity.
    Standard sea-level: 1.225 kg/m³ at 15°C.
    """
    alt_m = altitude_ft * 0.3048
    temp_c = (temp_f - 32) * 5 / 9
    temp_k = temp_c + 273.15

    if pressure_hpa is None:
        pressure_hpa = 1013.25 * (1 - 2.25577e-5 * alt_m) ** 5.25588

    pressure_pa = pressure_hpa * 100

    e_sat = 611.2 * math.exp(17.67 * temp_c / (temp_c + 243.5))
    e = e_sat * humidity_pct / 100
    p_dry = pressure_pa - e

    R_dry = 287.058
    R_vapor = 461.495

    rho = (p_dry / (R_dry * temp_k)) + (e / (R_vapor * temp_k))

    return rho


def _altitude_arsenal_interaction(altitude_ft: float, pitch_mix: dict) -> dict:
    """E4: altitude × arsenal interaction.

    At altitude, breaking balls lose movement. Curveball-heavy pitchers
    suffer most at Coors-like altitudes.
    """
    if not pitch_mix:
        return {"e4_altitude_arsenal": 0.0, "e4_breaking_at_altitude": False}

    breaking_types = {"CU", "KC", "SL", "ST", "SV"}
    breaking_usage = sum(p["usage"] for pt, p in pitch_mix.items() if pt in breaking_types)

    alt_factor = altitude_ft / 5280.0
    penalty = breaking_usage * alt_factor * -0.02

    return {
        "e4_altitude_arsenal": float(penalty),
        "e4_breaking_at_altitude": breaking_usage > 0.35 and altitude_ft > 3000,
    }


def _temp_pitch_mix_interaction(temp_f: float | None, pitch_mix: dict) -> dict:
    """E6: temperature × pitch mix interaction.

    Cold (<60°F): slider whiff -1.3%, splitter whiff -1.0%.
    """
    if temp_f is None or not pitch_mix:
        return {"e6_temp_pitch_penalty": 0.0}

    if temp_f >= 60:
        return {"e6_temp_pitch_penalty": 0.0}

    cold_factor = (60 - temp_f) / 30.0

    slider_types = {"SL", "ST", "SV"}
    splitter_types = {"FS", "SF"}

    slider_usage = sum(p["usage"] for pt, p in pitch_mix.items() if pt in slider_types)
    splitter_usage = sum(p["usage"] for pt, p in pitch_mix.items() if pt in splitter_types)

    penalty = -(slider_usage * 0.013 + splitter_usage * 0.010) * cold_factor

    return {"e6_temp_pitch_penalty": float(penalty)}


def build_park_weather_features(
    venue_id: int,
    pitcher_arsenal: dict,
    temp_f: float | None = None,
    humidity_pct: float | None = None,
    pressure_hpa: float | None = None,
) -> dict:
    """Build all Group E features."""
    venue = VENUE_DATA.get(venue_id, {})
    alt_ft = venue.get("alt_ft", 0)
    roof = venue.get("roof", "open")

    features = {}

    k_factor = PARK_K_FACTORS.get(venue_id, 1.00)
    rho = _air_density(alt_ft, temp_f or 72.0, humidity_pct or 50.0, pressure_hpa)
    rho_sea = _air_density(0, 72.0, 50.0, 1013.25)
    density_ratio = rho / rho_sea

    residual_k = k_factor - density_ratio
    features["e1_park_k_factor"] = k_factor
    features["e1_park_k_residual"] = float(residual_k)

    fb_whiff = PARK_FB_WHIFF.get(venue_id, LEAGUE_FB_WHIFF)
    features["e2_park_fb_whiff"] = fb_whiff
    features["e2_park_fb_whiff_delta"] = fb_whiff - LEAGUE_FB_WHIFF

    features["e3_altitude_ft"] = alt_ft

    alt_arsenal = _altitude_arsenal_interaction(alt_ft, pitcher_arsenal)
    features.update(alt_arsenal)

    features["e5_roof_dome"] = roof == "dome"
    features["e5_roof_retractable"] = roof == "retractable"
    features["e5_roof_open"] = roof == "open"

    temp_pitch = _temp_pitch_mix_interaction(temp_f, pitcher_arsenal)
    features.update(temp_pitch)

    features["e17_air_density"] = float(rho)
    features["e17_density_ratio"] = float(density_ratio)
    features["e17_temp_f"] = temp_f
    features["e17_humidity_pct"] = humidity_pct

    return features
