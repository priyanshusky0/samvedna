"""Tiny gazetteer for tagging news headlines to a state.

Keys are lower-case, punctuation-free (matching `normalise_title`). Only state
names and large / disaster-prone cities are listed: precision over recall.
"""
from __future__ import annotations

from app.config import get_settings

PLACES: dict[str, list[str]] = {
    "Maharashtra": ["maharashtra", "mumbai", "pune", "nagpur", "nashik", "thane", "aurangabad",
                    "kolhapur", "sangli", "satara", "ratnagiri", "raigad", "latur", "solapur", "marathwada",
                    "vidarbha", "konkan"],
    "Gujarat": ["gujarat", "ahmedabad", "surat", "vadodara", "rajkot", "gandhinagar", "kutch",
                "kachchh", "bhuj", "jamnagar", "saurashtra", "porbandar", "dwarka", "junagadh"],
    "Goa": ["goa", "panaji", "margao", "vasco"],
    "Karnataka": ["karnataka", "bengaluru", "bangalore", "mysuru", "mysore", "mangaluru", "mangalore",
                  "hubballi", "belagavi", "belgaum", "kodagu", "coorg", "udupi", "shivamogga", "uttara kannada",
                  "dakshina kannada", "chikkamagaluru"],
    "Kerala": ["kerala", "thiruvananthapuram", "trivandrum", "kochi", "cochin", "kozhikode", "wayanad",
               "idukki", "thrissur", "kannur", "kollam", "alappuzha", "malappuram", "palakkad",
               "pathanamthitta", "ernakulam"],
    "Tamil Nadu": ["tamil nadu", "chennai", "coimbatore", "madurai", "tiruchirappalli", "trichy", "salem",
                   "tirunelveli", "nilgiris", "ooty", "cuddalore", "nagapattinam", "thoothukudi",
                   "tuticorin", "kanyakumari", "vellore", "puducherry"],
    "Andhra Pradesh": ["andhra pradesh", "andhra", "visakhapatnam", "vizag", "vijayawada", "amaravati",
                       "guntur", "nellore", "tirupati", "kurnool", "kakinada", "srikakulam", "anantapur",
                       "rajahmundry", "godavari"],
    "Telangana": ["telangana", "hyderabad", "secunderabad", "warangal", "karimnagar", "khammam",
                  "nizamabad", "adilabad"],
    "Rajasthan": ["rajasthan", "jaipur", "jodhpur", "udaipur", "kota", "bikaner", "ajmer", "jaisalmer",
                  "barmer", "alwar", "sikar"],
    "Madhya Pradesh": ["madhya pradesh", "bhopal", "indore", "jabalpur", "gwalior", "ujjain", "sagar",
                       "rewa", "satna", "narmadapuram"],
}


def gazetteer() -> dict[str, str]:
    """place -> state, restricted to the configured states."""
    wanted = set(get_settings().states)
    return {place: state for state, places in PLACES.items() if state in wanted for place in places}
