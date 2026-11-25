import base64
import hashlib
import json
import re
import time
from typing import Dict, Any, Optional
import httpx
from PIL import Image
import io

try:
    from cloud_app.config import GEMINI_API_KEY, GEMINI_MODEL
except ImportError:
    from config import GEMINI_API_KEY, GEMINI_MODEL

class GeminiCloudService:
    """
    Serverless-ready Gemini client for Vercel.
    Directly processes Front and Rear camera frames for Dual-Camera Fusion ANPR.
    """
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl_sec = 60.0

    def optimize_image(self, img_bytes: bytes, max_dim: int = 1280) -> bytes:
        try:
            with Image.open(io.BytesIO(img_bytes)) as img:
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                w, h = img.size
                if max(w, h) > max_dim:
                    scale = max_dim / float(max(w, h))
                    new_size = (int(w * scale), int(h * scale))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85, optimize=True)
                return buf.getvalue()
        except Exception:
            return img_bytes

    def _hash_bytes(self, b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()

    async def detect_fusion(
        self,
        front_bytes: Optional[bytes],
        rear_bytes: Optional[bytes],
        live_weight: float = 0.0
    ) -> Dict[str, Any]:
        """
        Multimodal fusion ANPR: Combines Front & Rear camera frames + scale weight
        into a single Gemini prompt for plate, vehicle class, load status & material.
        """
        if not front_bytes and not rear_bytes:
            return {"success": False, "vehicle_no": "", "error": "No camera frames provided"}

        combined_hash = ""
        if front_bytes:
            front_bytes = self.optimize_image(front_bytes)
            combined_hash += self._hash_bytes(front_bytes)[:16]
        if rear_bytes:
            rear_bytes = self.optimize_image(rear_bytes)
            combined_hash += self._hash_bytes(rear_bytes)[:16]

        # Check in-memory cache
        now = time.time()
        if combined_hash in self._cache:
            entry = self._cache[combined_hash]
            if now - entry["ts"] < self._cache_ttl_sec:
                return entry["data"]

        weight_note = f"Measured platform scale weight: {int(live_weight)} kg.\n" if live_weight > 0 else ""
        prompt = (
            "Analyze these weighbridge camera images (Front and/or Rear of the truck/vehicle). "
            + weight_note +
            "Cross-verify the Indian vehicle registration number plate across both images and inspect the vehicle type and cargo bed. "
            "Return ONLY valid JSON matching this schema:\n"
            "{\n"
            '  "vehicle_no": "TN33AB1234",\n'
            '  "vehicle_class": "🚚 Lorry / லாரி",\n'
            '  "load_status": "Loaded / லோடு",\n'
            '  "material": "General Cargo / பொதுச் சரக்கு",\n'
            '  "confidence": 0.95,\n'
            '  "notes": "Verified from cameras"\n'
            "}\n"
            "For vehicle_class, pick exact string from: "
            '"🚚 Lorry / லாரி", "🚐 Tempo / டெம்போ", "🛺 Auto / ஆட்டோ", '
            '"🚜 Tractor / டிராக்டர்", "🚛 Tipper / டிப்பர்", "🚗 Car / Van / இதர".\n'
            'For load_status, pick exact string: "Loaded / லோடு" or "Empty / காலி".\n'
            "For material, pick exact string from: "
            '"General Cargo / பொதுச் சரக்கு", "Sand / மணல்", "Cotton / பருத்தி", "Cement / சிமென்ட்", '
            '"Maize / மக்காச்சோளம்", "Paddy / நெல்", "Gravel / ஜல்லி", "Iron Scrap / இரும்பு கழிவு", '
            '"Coconut / தேங்காய்", "Timber / மரம்", "Sugarcane / கரும்பு".\n'
            "Ensure vehicle_no is uppercase without spaces (e.g., TN33AB1234). If plate is unreadable, return empty string."
        )

        parts = [{"text": prompt}]
        if front_bytes:
            parts.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(front_bytes).decode("utf-8")
                }
            })
        if rear_bytes:
            parts.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(rear_bytes).decode("utf-8")
                }
            })

        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.1
            }
        }

        # Model fallback chain (bypass Tailscale proxy for direct Google API access)
        models = ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-flash-latest"]
        headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            for model_name in models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
                try:
                    res = await client.post(url, json=body, headers=headers)
                    if res.status_code == 200:
                        data = res.json()
                        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        parsed = json.loads(text)
                        
                        clean_no = re.sub(r"[^A-Z0-9]", "", str(parsed.get("vehicle_no", "")).upper())
                        v_class = parsed.get("vehicle_class") or parsed.get("vehicle_type") or ""
                        result = {
                            "success": bool(clean_no),
                            "vehicle_no": clean_no,
                            "vehicle_class": v_class,
                            "vehicle_type": v_class or "Truck",
                            "load_status": parsed.get("load_status", ""),
                            "material": parsed.get("material", ""),
                            "confidence": float(parsed.get("confidence", 0.9)),
                            "model_used": model_name
                        }
                        self._cache[combined_hash] = {"ts": now, "data": result}
                        return result
                except Exception:
                    continue

        return {"success": False, "vehicle_no": "", "error": "Gemini ANPR unavailable"}

gemini_cloud = GeminiCloudService()
