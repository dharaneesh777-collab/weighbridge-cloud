import time
import re
from typing import Dict, Any, Optional
import httpx

try:
    from cloud_app.config import EDGE_URL, EDGE_KEY
except ImportError:
    from config import EDGE_URL, EDGE_KEY

class EdgeClient:
    """
    Resilient client that communicates with Home PC via Tailscale.
    Implements a 1.0s in-memory SWR cache to ensure Home PC experiences near-zero load.
    Gracefully handles Home PC offline/reboot scenarios without crashing.
    Supports both /api/edge/ and legacy /api/ endpoints.
    """
    def __init__(self):
        self._cached_weight: Dict[str, Any] = {
            "weight": 0.0,
            "is_stable": False,
            "status_label": "CONNECTING",
            "edge_online": False,
            "timestamp": 0.0
        }
        self._last_fetch_time: float = 0.0
        self._cache_ttl_sec: float = 1.0  # Shield Home PC with 1s cache
        self._client = httpx.AsyncClient(timeout=3.5)

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Bridge-Key": EDGE_KEY,
            "User-Agent": "WeighbridgeCloudApp/1.0"
        }

    async def get_live_weight(self, force_refresh: bool = False) -> Dict[str, Any]:
        now = time.time()
        if not force_refresh and (now - self._last_fetch_time < self._cache_ttl_sec):
            return self._cached_weight

        # Try standard edge JSON endpoint first
        url = f"{EDGE_URL}/api/edge/live-weight"
        try:
            res = await self._client.get(url, headers=self._headers())
            if res.status_code == 200:
                data = res.json()
                self._cached_weight = {
                    "weight": float(data.get("weight", 0.0)),
                    "is_stable": bool(data.get("is_stable", False)),
                    "status_label": data.get("status_label", "ONLINE"),
                    "edge_online": True,
                    "timestamp": now
                }
                self._last_fetch_time = now
                return self._cached_weight
            elif res.status_code == 404:
                # Fallback to local /api/live-weight HTML endpoint
                fallback_url = f"{EDGE_URL}/api/live-weight"
                fb_res = await self._client.get(fallback_url, timeout=3.0)
                if fb_res.status_code == 200:
                    text = fb_res.text
                    weight_match = re.search(r'data-weight=["\']([^"\']+)["\']', text)
                    stable_match = re.search(r'data-stable=["\']([^"\']+)["\']', text)
                    w = float(weight_match.group(1)) if weight_match else 0.0
                    s = (stable_match.group(1).lower() in ("true", "1")) if stable_match else False
                    self._cached_weight = {
                        "weight": w,
                        "is_stable": s,
                        "status_label": "ONLINE (LIVE)",
                        "edge_online": True,
                        "timestamp": now
                    }
                    self._last_fetch_time = now
                    return self._cached_weight
            
            self._cached_weight["edge_online"] = False
            self._cached_weight["status_label"] = f"AUTH_ERR_{res.status_code}"
            return self._cached_weight
        except Exception:
            self._cached_weight["edge_online"] = False
            self._cached_weight["status_label"] = "HOME_PC_OFFLINE"
            return self._cached_weight

    async def get_camera_snapshot(self, camera: str = "front") -> Optional[bytes]:
        # Try edge snapshot first, fallback to camera preview
        urls = [
            f"{EDGE_URL}/api/edge/camera/snapshot?camera={camera}",
            f"{EDGE_URL}/api/camera/preview?camera={camera}"
        ]
        for url in urls:
            try:
                res = await self._client.get(url, headers=self._headers(), timeout=5.0)
                if res.status_code == 200 and len(res.content) > 100:
                    return res.content
            except Exception:
                continue
        return None

    async def print_slip(
        self,
        ticket_no: int,
        html_content: str,
        copies: int = 1
    ) -> Dict[str, Any]:
        """
        Sends HTML slip to Home PC for silent spooling to Epson L3350.
        """
        url = f"{EDGE_URL}/api/edge/print"
        payload = {
            "ticket_no": ticket_no,
            "copies": copies,
            "slip_html": html_content
        }
        try:
            res = await self._client.post(url, json=payload, headers=self._headers(), timeout=12.0)
            if res.status_code == 200:
                return res.json()
            # Fallback to local print endpoint
            fb_url = f"{EDGE_URL}/api/print-slip/{ticket_no}"
            fb_res = await self._client.post(fb_url, timeout=10.0)
            if fb_res.status_code == 200:
                return {"success": True, "message": "Printed via local endpoint"}
            return {"success": False, "message": f"Edge error {res.status_code}: {res.text}"}
        except Exception as e:
            return {"success": False, "message": f"Could not reach Home PC printer: {str(e)}"}

edge_client = EdgeClient()
