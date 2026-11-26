import base64
import hashlib
import io
import json
import os
import re
import time
import wave
from typing import Any, Dict, List, Optional
import httpx
from PIL import Image

try:
    from cloud_app.config import GEMINI_API_KEY
except ImportError:
    from config import GEMINI_API_KEY


class GeminiCloudService:
    """
    Async Gemini client for Cloud Portal.
    Supports Dual-Camera Fusion ANPR, Bilingual (English/Tamil/Tanglish) Voice
    Command Parsing, and Live Operational Copilot queries.
    """

    MODELS = [
        "gemini-3-flash-preview",
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-3.5-flash",
        "gemini-3.8-flash",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest"
    ]
    TTS_MODELS = [
        "gemini-3.8-flash-lite-tts",
        "gemini-3.8-flash-tts",
        "gemini-3.1-flash-tts-preview"
    ]

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl_sec = 60.0
        self._tts_cache: Dict[str, Dict[str, Any]] = {}

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

    async def _call_gemini_json(self, parts: List[Dict[str, Any]], timeout: float = 9.0) -> Dict[str, Any]:
        """
        Shared async Gemini REST helper (bypasses Tailscale proxy via trust_env=False).
        Returns {"success": True, "data": <parsed_dict>, "model_used": <str>} on success.
        """
        api_key = (os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY or "").strip()
        if not api_key:
            return {"success": False, "error": "Gemini API key is not configured."}

        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.1
            }
        }
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        last_error = "Gemini service unavailable"

        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            for model_name in self.MODELS:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
                try:
                    res = await client.post(url, json=body, headers=headers)
                    if res.status_code == 200:
                        payload = res.json()
                        text = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
                        if text.startswith("```"):
                            text = re.sub(r"^```(?:json)?\s*", "", text)
                            text = re.sub(r"\s*```$", "", text)
                        parsed = json.loads(text)
                        return {"success": True, "data": parsed, "model_used": model_name}
                    last_error = f"Gemini HTTP {res.status_code}"
                    if res.status_code in (404, 429, 503):
                        continue
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    continue

        return {"success": False, "error": last_error}

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

        parts: List[Dict[str, Any]] = [{"text": prompt}]
        if front_bytes:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(front_bytes).decode("utf-8")}})
        if rear_bytes:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(rear_bytes).decode("utf-8")}})

        res = await self._call_gemini_json(parts, timeout=10.0)
        if res.get("success"):
            parsed = res["data"]
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
                "model_used": res["model_used"]
            }
            self._cache[combined_hash] = {"ts": now, "data": result}
            return result

        return {"success": False, "vehicle_no": "", "error": res.get("error", "Gemini ANPR unavailable")}

    async def parse_voice_command(
        self,
        speech_text: str,
        current_lang: str = "mixed",
        valid_materials: Optional[List[str]] = None,
        valid_vehicle_types: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Parses spoken Tamil, English, or Tanglish operator commands into structured
        weighbridge form fields and actions using Gemini.
        """
        materials_str = ", ".join(f'"{m}"' for m in (valid_materials or []))
        vtypes_str = ", ".join(f'"{v}"' for v in (valid_vehicle_types or []))

        prompt = (
            "You are a Smart Bilingual Voice Assistant (Tamil, English, and Tanglish) for "
            "AGAN VENDHAN WEIGH BRIDGE in Tamil Nadu, India.\n"
            f"Language preference hint: {current_lang}\n"
            f'Operator utterance: "{speech_text}"\n\n'
            "Extract the intent and structured fields into VALID JSON matching:\n"
            "{\n"
            '  "action": "fill_form" | "scan_plate" | "refresh_cameras" | "read_weight" | "submit_weigh" | "confirm_print" | "reprint_ticket" | "toggle_autopilot" | "clear_form" | "navigate" | "copilot_query" | "general_help",\n'
            '  "vehicle_no": "uppercase Indian plate without spaces/hyphens (e.g. TN33AB1234) or null",\n'
            f'  "vehicle_type": "exact match from [{vtypes_str}] or null",\n'
            '  "load_status": "\'Loaded / லோடு\' or \'Empty / காலி\' or null",\n'
            f'  "material": "closest exact match from [{materials_str}] or null",\n'
            '  "customer_name": "party/customer name if spoken, or null",\n'
            '  "mobile": "10-digit Indian mobile number if spoken, or null",\n'
            '  "charge": "numeric weighment fee in INR if explicitly spoken (e.g. 150), or null",\n'
            '  "copies": "integer 1, 2, or 3 if print copies mentioned, or null",\n'
            '  "ticket_no": "integer ticket number if asking to reprint a ticket (e.g. 1005), or null",\n'
            '  "autopilot_state": "true or false if toggling autopilot, or null",\n'
            '  "manual_tare": "numeric tare weight in kg if spoken in single weighment, or null",\n'
            '  "weighment_type": "\'double\' or \'single\' or null",\n'
            '  "navigate_to": "\'/\' or \'/tickets\' or null",\n'
            '  "copilot_question": "the analytics/history/lookup question if action is copilot_query, else null",\n'
            '  "spoken_reply": "Concise, natural confirmation under 12 words in the SAME language the user spoke (Tamil script if user spoke Tamil, English if English)"\n'
            "}\n"
            "Strict Rules:\n"
            "- Understand Tamil words: மணல்=Sand, ஜல்லி=Gravel, சிமென்ட்=Cement, நெல்=Paddy, மக்காச்சோளம்=Maize, "
            "கரும்பு=Sugarcane, மரம்=Timber, பருத்தி=Cotton, தேங்காய்=Coconut, இரும்பு=Iron Scrap, லோடு=Loaded, காலி=Empty, "
            "லாரி=Lorry, டிப்பர்=Tipper, டிராக்டர்=Tractor, டெம்போ=Tempo, ஆட்டோ=Auto, கார்=Car / Van.\n"
            "- If Tamil numerals/words for copies are spoken (e.g. 'இரண்டு காப்பி' -> copies: 2), convert to digits.\n"
            "- Revenue, collection, tonnage, pending open trucks, customer/vehicle ticket history queries -> action: 'copilot_query'.\n"
            "- Camera scan / ANPR ('கேமரா ஸ்கேன்', 'scan plate') -> action: 'scan_plate'.\n"
            "- Refresh camera preview ('கேமரா ரெப்ரெஷ்', 'refresh cameras', 'show camera') -> action: 'refresh_cameras'.\n"
            "- Live weight ('எடை எவ்வளவு', 'what is weight') -> action: 'read_weight'.\n"
            "- Open pre-print verification ('சேமி', 'பில் போடு', 'save and print') -> action: 'submit_weigh'.\n"
            "- Confirm physical print ('உறுதி செய் பிரிண்ட்', 'confirm print', 'yes print') -> action: 'confirm_print'.\n"
            "- Reprint specific ticket ('டிக்கெட் 1005 பிரிண்ட்', 'reprint 1005') -> action: 'reprint_ticket' + ticket_no.\n"
            "- Toggle AI Auto-Pilot ('ஆட்டோ பைலட் ஆன்/ஆஃப்', 'turn on/off autopilot') -> action: 'toggle_autopilot' + autopilot_state.\n"
            "- Clear / reset ('அழி', 'புதிய எடை', 'clear form') -> action: 'clear_form'.\n"
            "- Navigation ('பதிவுகள்', 'tickets page', 'weigh page') -> action: 'navigate'.\n"
            "- If any form fields (vehicle_no, customer_name, mobile, material, load_status, charge, copies, vehicle_type, weighment_type) are provided, action is 'fill_form'.\n"
            "Return JSON ONLY."
        )

        res = await self._call_gemini_json([{"text": prompt}], timeout=9.0)
        if not res.get("success"):
            return {"success": False, "error": res.get("error", "Could not parse voice command")}

        parsed = res["data"]
        if parsed.get("vehicle_no"):
            parsed["vehicle_no"] = re.sub(r"[^A-Z0-9]", "", str(parsed["vehicle_no"]).upper()) or None
        if parsed.get("mobile"):
            digits = re.sub(r"\D", "", str(parsed["mobile"]))
            parsed["mobile"] = digits[-10:] if len(digits) >= 10 else None
        if parsed.get("copies") is not None:
            try:
                parsed["copies"] = max(1, min(3, int(parsed["copies"])))
            except Exception:
                parsed["copies"] = None
        if parsed.get("charge") is not None:
            try:
                parsed["charge"] = max(0.0, float(parsed["charge"]))
            except Exception:
                parsed["charge"] = None
        if parsed.get("ticket_no") is not None:
            try:
                parsed["ticket_no"] = int(parsed["ticket_no"])
            except Exception:
                parsed["ticket_no"] = None
        parsed["success"] = True
        parsed["model_used"] = res["model_used"]
        return parsed

    async def synthesize_speech(self, text: str, voice_name: str = "Kore") -> Dict[str, Any]:
        """
        Synthesizes natural Tamil/English speech via Gemini Neural TTS (audio/wav)
        with an in-memory cache for instant repeat playback.
        """
        clean_text = (text or "").strip()
        if not clean_text:
            return {"success": False, "error": "Empty text"}

        cache_key = hashlib.sha256(f"{voice_name}:{clean_text}".encode("utf-8")).hexdigest()[:24]
        if cache_key in self._tts_cache:
            return self._tts_cache[cache_key]

        api_key = (os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY or "").strip()
        if not api_key:
            return {"success": False, "error": "Gemini API key not configured"}

        body = {
            "contents": [{"parts": [{"text": clean_text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice_name}
                    }
                }
            }
        }
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        last_error = "TTS unavailable"

        async with httpx.AsyncClient(timeout=12.0, trust_env=False) as client:
            for model_name in self.TTS_MODELS:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
                try:
                    res = await client.post(url, json=body, headers=headers)
                    if res.status_code == 200:
                        data = res.json()
                        inline = data["candidates"][0]["content"]["parts"][0]["inlineData"]
                        mime = inline.get("mimeType", "audio/wav")
                        raw_audio = base64.b64decode(inline["data"])

                        # Convert raw PCM audio/l16 to standard RIFF audio/wav if needed
                        if "l16" in mime.lower() or "pcm" in mime.lower():
                            buf = io.BytesIO()
                            with wave.open(buf, "wb") as wf:
                                wf.setnchannels(1)
                                wf.setsampwidth(2)
                                wf.setframerate(24000)
                                wf.writeframes(raw_audio)
                            raw_audio = buf.getvalue()
                            mime = "audio/wav"

                        result = {
                            "success": True,
                            "audio_b64": base64.b64encode(raw_audio).decode("ascii"),
                            "mime_type": mime,
                            "model_used": model_name
                        }
                        if len(self._tts_cache) > 80:
                            self._tts_cache.clear()
                        self._tts_cache[cache_key] = result
                        return result
                    last_error = f"TTS HTTP {res.status_code}"
                    if res.status_code in (404, 429, 503):
                        continue
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    continue

        return {"success": False, "error": last_error}

    async def answer_copilot_query(
        self,
        question: str,
        metrics: Dict[str, Any],
        lang: str = "mixed"
    ) -> Dict[str, Any]:
        """
        Answers operational business questions using live IST Firestore metrics.
        """
        prompt = (
            "You are the AI Operational Copilot for AGAN VENDHAN WEIGH BRIDGE.\n"
            f'Operator question ({lang}): "{question}"\n\n'
            f"Live Today (IST) Weighbridge Metrics:\n{json.dumps(metrics, indent=2, ensure_ascii=False)}\n\n"
            "Return VALID JSON:\n"
            "{\n"
            '  "answer": "Clear direct answer with exact numbers (under 30 words)",\n'
            '  "spoken_reply": "Natural spoken answer under 15 words in the SAME language as the question (Tamil script if Tamil, English if English)"\n'
            "}"
        )
        res = await self._call_gemini_json([{"text": prompt}], timeout=9.0)
        if res.get("success"):
            out = res["data"]
            out["success"] = True
            out["model_used"] = res["model_used"]
            return out
        return {"success": False, "error": res.get("error", "Copilot unavailable")}


gemini_cloud = GeminiCloudService()
