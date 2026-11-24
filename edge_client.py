import time
import re
import base64
import io
from typing import Dict, Any, Optional
import httpx
from PIL import Image, ImageDraw

try:
    from cloud_app.config import EDGE_URL, EDGE_KEY
except ImportError:
    from config import EDGE_URL, EDGE_KEY

class EdgeClient:
    """
    Resilient client that communicates with Home PC via Tailscale.
    Implements a 1.0s in-memory SWR cache for weights and 2.5s cache for camera frames
    to ensure Home PC experiences near-zero load.
    Gracefully handles Home PC offline/reboot scenarios without crashing.
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
        self._cache_ttl_sec: float = 1.0  # Shield Home PC scale with 1s cache
        self._camera_cache: Dict[str, Dict[str, Any]] = {}
        self._camera_cache_ttl: float = 2.5  # Shield Home PC cameras with 2.5s cache
        self._client = httpx.AsyncClient(timeout=4.0)

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

    def _generate_placeholder(self, cam_type: str, reason: str = "WAITING") -> bytes:
        try:
            img = Image.new("RGB", (640, 360), color=(15, 23, 42))
            draw = ImageDraw.Draw(img)
            draw.rectangle([(20, 20), (620, 340)], outline=(51, 65, 85), width=2)
            label = f"CAM {cam_type.upper()} · {reason}"
            draw.text((230, 170), label, fill=(148, 163, 184))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            return buf.getvalue()
        except Exception:
            return b""

    async def get_camera_snapshot(self, camera: str = "front") -> Optional[bytes]:
        cam_type = "front" if camera in ("front", "both") else "rear"
        now = time.time()

        # 1. Check in-memory cache to guarantee ZERO extra load on Home PC
        if cam_type in self._camera_cache:
            entry = self._camera_cache[cam_type]
            if now - entry["timestamp"] < self._camera_cache_ttl:
                return entry["bytes"]

        # 2. Query Home PC camera test endpoint (returns high-res JPEG base64)
        url = f"{EDGE_URL}/api/camera/test?type={cam_type}"
        try:
            res = await self._client.get(url, headers=self._headers(), timeout=5.0)
            if res.status_code == 200:
                data = res.json()
                b64_str = data.get("image_b64")
                if b64_str:
                    img_bytes = base64.b64decode(b64_str)
                    self._camera_cache[cam_type] = {
                        "bytes": img_bytes,
                        "timestamp": now
                    }
                    return img_bytes
        except Exception:
            pass

        # 3. Fallback: Edge snapshot endpoint
        try:
            edge_url = f"{EDGE_URL}/api/edge/camera/snapshot?camera={cam_type}"
            res = await self._client.get(edge_url, headers=self._headers(), timeout=4.0)
            if res.status_code == 200 and res.headers.get("content-type", "").startswith("image/"):
                img_bytes = res.content
                self._camera_cache[cam_type] = {
                    "bytes": img_bytes,
                    "timestamp": now
                }
                return img_bytes
        except Exception:
            pass

        # 4. Fallback placeholder (clean visual image, zero broken icons)
        return self._generate_placeholder(cam_type, "OFFLINE")

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
