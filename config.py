import os
from datetime import timezone, timedelta
from pathlib import Path

CLOUD_DIR = Path(__file__).resolve().parent
EDGE_URL = os.getenv("EDGE_URL", "http://100.101.78.57:8000").rstrip("/")
EDGE_KEY = os.getenv("EDGE_KEY", "wb_edge_secret_key_8842")
ADMIN_KEY = os.getenv("ADMIN_KEY", EDGE_KEY)
IST = timezone(timedelta(hours=5, minutes=30))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

if os.getenv("VERCEL"):
    DEFAULT_DB = "sqlite:////tmp/cloud_weighbridge.db"
else:
    DEFAULT_DB = "sqlite:///cloud_weighbridge.db"

DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB)

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "AGAN VENDHAN WEIGH BRIDGE")
BUSINESS_CAPACITY = os.getenv("BUSINESS_CAPACITY", "CAPACITY 80TON")
BUSINESS_ADDRESS = os.getenv("BUSINESS_ADDRESS", "NO-25/19 ,ERODE MAIN ROAD , NEAR INDIAN OIL PETROL BUNK , NALLAMPATTI-638057")
BUSINESS_PHONE = os.getenv("BUSINESS_PHONE", "9965252394")
DEFAULT_CHARGE = float(os.getenv("DEFAULT_CHARGE", "100.0"))
DEFAULT_COPIES = int(os.getenv("DEFAULT_COPIES", "1"))
