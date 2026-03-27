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

# Google News regional editions
# English regions use the same keywords; non-English regions have translated keywords
GOOGLE_NEWS_REGIONS = [
    # English-language regions
    {"gl": "US", "hl": "en", "ceid": "US:en", "label": "United States"},
    {"gl": "GB", "hl": "en", "ceid": "GB:en", "label": "United Kingdom"},
    {"gl": "AU", "hl": "en", "ceid": "AU:en", "label": "Australia"},
    {"gl": "IN", "hl": "en", "ceid": "IN:en", "label": "India"},
    {"gl": "SG", "hl": "en", "ceid": "SG:en", "label": "Singapore"},
    {"gl": "CA", "hl": "en", "ceid": "CA:en", "label": "Canada"},
    # Non-English regions (translated keywords)
    {"gl": "FR", "hl": "fr", "ceid": "FR:fr", "label": "France", "keywords": [
        "explosion raffinerie", "incendie usine chimique", "explosion industrielle",
        "incendie raffinerie", "fuite chimique", "fuite de gaz",
        "marée noire", "nuage toxique", "matières dangereuses",
        "sécurité des procédés", "accident industriel",
    ]},
    {"gl": "DE", "hl": "de", "ceid": "DE:de", "label": "Germany", "keywords": [
        "Raffinerieexplosion", "Chemiebrand", "Industrieexplosion",
        "Raffineriebrand", "Chemieunfall", "Gasleck",
        "Gefahrgut", "Schadstoffwolke", "Chemiewerk",
        "Anlagensicherheit", "Störfall",
    ]},
    {"gl": "NL", "hl": "nl", "ceid": "NL:nl", "label": "Netherlands", "keywords": [
        "raffinaderij explosie", "chemische brand", "industriële explosie",
        "chemisch lek", "gaslek", "olielek",
        "gevaarlijke stoffen", "gifwolk", "chemische fabriek",
        "procesveiligheid", "Brzo",
    ]},
    {"gl": "IT", "hl": "it", "ceid": "IT:it", "label": "Italy", "keywords": [
        "esplosione raffineria", "incendio chimico", "esplosione industriale",
        "incendio raffineria", "fuoriuscita chimica", "fuga di gas",
        "materiali pericolosi", "nube tossica", "impianto chimico",
        "sicurezza di processo", "incidente rilevante",
    ]},
    # Middle East — English editions (English widely used in Gulf industry)
    {"gl": "AE", "hl": "en", "ceid": "AE:en", "label": "UAE"},
    {"gl": "SA", "hl": "en", "ceid": "SA:en", "label": "Saudi Arabia"},
    {"gl": "QA", "hl": "en", "ceid": "QA:en", "label": "Qatar"},
    {"gl": "KW", "hl": "en", "ceid": "KW:en", "label": "Kuwait"},
    {"gl": "BH", "hl": "en", "ceid": "BH:en", "label": "Bahrain"},
    {"gl": "EG", "hl": "en", "ceid": "EG:en", "label": "Egypt"},
    # Middle East — Arabic edition (broader coverage)
    {"gl": "SA", "hl": "ar", "ceid": "SA:ar", "label": "Middle East", "keywords": [
        "انفجار مصفاة", "حريق مصنع", "انفجار صناعي",
        "تسرب كيميائي", "تسرب غاز", "تسرب نفطي",
        "مواد خطرة", "سحابة سامة", "مصنع كيميائي",
        "سلامة العمليات", "حادث صناعي",
    ]},
]

# Direct RSS feeds from international outlets (filtered by English keyword matching)
DIRECT_RSS_FEEDS = [
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml", "source": "BBC World News"},
    {"url": "https://www.france24.com/en/rss", "source": "France 24"},
    {"url": "https://rss.dw.com/rdf/rss-en-all", "source": "Deutsche Welle"},
    # Middle East
    {"url": "https://www.aljazeera.com/xml/rss/all.xml", "source": "Al Jazeera English"},
    {"url": "https://gulfnews.com/rss", "source": "Gulf News"},
    {"url": "https://www.arabnews.com/rss.xml", "source": "Arab News"},
]
