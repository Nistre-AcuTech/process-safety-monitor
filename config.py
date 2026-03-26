import os
from dotenv import load_dotenv

load_dotenv()

# Process safety keywords for news filtering
# These are matched against article titles — keep them specific enough
# to avoid false positives (no bare "fire", "leak", "chemical", etc.)
KEYWORDS = [
    # Explosions & blasts
    "explosion",
    "detonation",
    "dust explosion",
    "vapor cloud explosion",
    "BLEVE",
    # Fires (industry-specific)
    "refinery fire",
    "plant fire",
    "chemical fire",
    "industrial fire",
    "factory fire",
    "warehouse fire",
    "tank fire",
    "pipeline fire",
    # Leaks & spills (industry-specific)
    "chemical spill",
    "chemical leak",
    "gas leak",
    "oil spill",
    "toxic release",
    "hazardous release",
    "pipeline leak",
    # Hazmat & toxic
    "hazmat",
    "toxic cloud",
    "vapor cloud",
    "chemical release",
    # Industry terms
    "refinery incident",
    "plant incident",
    "industrial incident",
    "process safety",
    "chemical plant",
    "refinery explosion",
    "shelter in place",
    # Regulatory / investigations
    "CSB investigation",
    "OSHA citation",
    "OSHA fine",
    "EPA violation",
]

# How far back to search (hours)
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))

# Zoho CRM
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "")
ZOHO_API_REGION = os.getenv("ZOHO_API_REGION", "com")  # com, eu, in, com.au, jp, ca

# SMTP
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.office365.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)

# Recipients (comma-separated)
RECIPIENTS = [
    r.strip()
    for r in os.getenv("RECIPIENTS", "").split(",")
    if r.strip()
]

# GDELT settings
GDELT_MAX_RECORDS = int(os.getenv("GDELT_MAX_RECORDS", "250"))
