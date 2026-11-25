import asyncio
import base64
import io
import os
import re
import time
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
    Implements a 1.0s in-memory SWR cache for weights and 8.0s cache for camera frames
    to ensure Home PC experiences near-zero load.
    Debounces momentary network hiccups so UI status never flickers.
    """
    def __init__(self):
        self._cached_weight: Dict[str, Any] = {
            "weight": 0.0,
            "is_stable": True,
            "status_label": "ONLINE (LIVE)",
            "edge_online": True,
            "timestamp": 0.0
        }
        self._last_fetch_time: float = 0.0
        self._cache_ttl_sec: float = 1.0  # Shield Home PC scale with 1s cache
        self._camera_cache: Dict[str, Dict[str, Any]] = {}
        self._camera_cache_ttl: float = 8.0  # Shield Home PC cameras with 8s cache
        self._camera_locks: Dict[str, asyncio.Lock] = {
            "front": asyncio.Lock(),
            "rear": asyncio.Lock()
        }
        self._consecutive_weight_failures: int = 0
        self._reconnecting_tailscale: bool = False
        self._working_weight_endpoint: str = "/api/edge/live-weight"

        # Separate HTTP clients so camera frame downloads never block scale reading
        self._scale_client = httpx.AsyncClient(timeout=4.0)
        self._camera_client = httpx.AsyncClient(timeout=10.0)
        self._print_client = httpx.AsyncClient(timeout=12.0)

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Bridge-Key": EDGE_KEY,
            "User-Agent": "WeighbridgeCloudApp/1.0"
        }

    async def _self_heal_tailscale(self):
        if self._reconnecting_tailscale:
            return
        self._reconnecting_tailscale = True
        try:
            proc = await asyncio.create_subprocess_exec(
                "tailscale", "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            out = (stdout or b"").decode(errors="ignore") + (stderr or b"").decode(errors="ignore")
            if "Logged out" in out or proc.returncode != 0:
                authkey = os.getenv("TAILSCALE_AUTHKEY", "")
                hostname = os.getenv("TAILSCALE_HOSTNAME", "render-weighbridge")
                if authkey:
                    up_proc = await asyncio.create_subprocess_exec(
                        "tailscale", "up",
                        f"--authkey={authkey}",
                        f"--hostname={hostname}",
                        "--accept-routes",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    await asyncio.wait_for(up_proc.communicate(), timeout=20.0)
        except Exception:
            pass
        finally:
            self._reconnecting_tailscale = False

    async def get_live_weight(self, force_refresh: bool = False) -> Dict[str, Any]:
        now = time.time()
        if not force_refresh and (now - self._last_fetch_time < self._cache_ttl_sec):
            return self._cached_weight

        # Primary: query known working weight endpoint directly in 1 fast round-trip
        target_url = f"{EDGE_URL}{self._working_weight_endpoint}"
        try:
            res = await self._scale_client.get(target_url, headers=self._headers())
            if res.status_code == 200:
                self._consecutive_weight_failures = 0
                content_type = res.headers.get("content-type", "")
                if "application/json" in content_type:
                    data = res.json()
                    self._cached_weight = {
                        "weight": float(data.get("weight", 0.0)),
                        "is_stable": bool(data.get("is_stable", False)),
                        "status_label": data.get("status_label", "ONLINE (LIVE)"),
                        "edge_online": True,
                        "timestamp": now
                    }
                else:
                    text = res.text
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
            elif res.status_code == 404:
                # Switch working endpoint to the other candidate
                self._working_weight_endpoint = "/api/edge/live-weight" if self._working_weight_endpoint == "/api/live-weight" else "/api/live-weight"
        except Exception:
            pass

        # Debounce: only mark offline if 3 consecutive polls fail (prevents UI flickering)
        self._consecutive_weight_failures += 1
        if self._consecutive_weight_failures >= 3:
            self._cached_weight["edge_online"] = False
            self._cached_weight["status_label"] = "HOME_PC_OFFLINE"
            # Trigger non-blocking self-healing if logged out
            if self._consecutive_weight_failures % 5 == 0 and not self._reconnecting_tailscale:
                asyncio.create_task(self._self_heal_tailscale())
        
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

    async def get_camera_snapshot(self, camera: str = "front", allow_placeholder: bool = True) -> Optional[bytes]:
        cam_type = "front" if camera in ("front", "both") else "rear"
        now = time.time()

        # 1. Check in-memory cache to guarantee ZERO extra load on Home PC
        if cam_type in self._camera_cache:
            entry = self._camera_cache[cam_type]
            if now - entry["timestamp"] < self._camera_cache_ttl:
                return entry["bytes"]

        # Serialize concurrent refreshes per camera so Home PC RTSP isn't hit twice
        async with self._camera_locks[cam_type]:
            now = time.time()
            if cam_type in self._camera_cache:
                entry = self._camera_cache[cam_type]
                if now - entry["timestamp"] < self._camera_cache_ttl:
                    return entry["bytes"]

            # 2. Query Home PC camera test endpoint (returns high-res JPEG base64)
            url = f"{EDGE_URL}/api/camera/test?type={cam_type}"
            try:
                res = await self._camera_client.get(url, headers=self._headers(), timeout=7.0)
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
                res = await self._camera_client.get(edge_url, headers=self._headers(), timeout=5.0)
                if res.status_code == 200 and res.headers.get("content-type", "").startswith("image/"):
                    img_bytes = res.content
                    self._camera_cache[cam_type] = {
                        "bytes": img_bytes,
                        "timestamp": now
                    }
                    return img_bytes
            except Exception:
                pass

            # 4. Fallback to existing cached frame (up to 120s) to prevent black standby during network glitches
            if cam_type in self._camera_cache and self._camera_cache[cam_type].get("bytes"):
                if now - self._camera_cache[cam_type].get("timestamp", 0) < 120.0:
                    return self._camera_cache[cam_type]["bytes"]

        # 5. Fallback placeholder (clean visual image, zero broken icons)
        if allow_placeholder:
            return self._generate_placeholder(cam_type, "STANDBY")
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
            res = await self._print_client.post(url, json=payload, headers=self._headers(), timeout=12.0)
            if res.status_code == 200:
                return res.json()
            # Fallback to local print endpoint
            fb_url = f"{EDGE_URL}/api/print-slip/{ticket_no}"
            fb_res = await self._print_client.post(fb_url, timeout=10.0)
            if fb_res.status_code == 200:
                return {"success": True, "message": "Printed via local endpoint"}
            return {"success": False, "message": f"Edge error {res.status_code}: {res.text}"}
        except Exception as e:
            return {"success": False, "message": f"Could not reach Home PC printer: {str(e)}"}

edge_client = EdgeClient()
