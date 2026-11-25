import os
import time
import asyncio
import subprocess
import base64
import urllib.request
import json
from datetime import datetime, timezone
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException, Query, Response, UploadFile, File, Header
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select, desc

try:
    from cloud_app.config import (
        CLOUD_DIR, BUSINESS_NAME, BUSINESS_CAPACITY,
        BUSINESS_ADDRESS, BUSINESS_PHONE, DEFAULT_CHARGE, DEFAULT_COPIES,
        ADMIN_KEY, IST
    )
    from cloud_app.db import (
        init_cloud_db, get_cloud_session, CloudTicket,
        get_next_cloud_ticket_number
    )
    from cloud_app.edge_client import edge_client
    from cloud_app.gemini_cloud import gemini_cloud
except ImportError:
    from config import (
        CLOUD_DIR, BUSINESS_NAME, BUSINESS_CAPACITY,
        BUSINESS_ADDRESS, BUSINESS_PHONE, DEFAULT_CHARGE, DEFAULT_COPIES,
        ADMIN_KEY, IST
    )
    from db import (
        init_cloud_db, get_cloud_session, CloudTicket,
        get_next_cloud_ticket_number
    )
    from edge_client import edge_client
    from gemini_cloud import gemini_cloud

def to_ist(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)

def format_ist(dt: Optional[datetime], fmt: str = "%d-%m-%Y %H:%M:%S") -> str:
    ist_dt = to_ist(dt)
    return ist_dt.strftime(fmt) if ist_dt else ""

def sync_tailscale_state_to_render():
    state_file = "/var/lib/tailscale/tailscaled.state"
    if not os.path.exists(state_file):
        return False
    try:
        with open(state_file, "rb") as f:
            raw_bytes = f.read()
        if not raw_bytes:
            return False
        b64_str = base64.b64encode(raw_bytes).decode("ascii")
        render_key = os.getenv("RENDER_API_KEY")
        render_srv = os.getenv("RENDER_SERVICE_ID")
        current_env = os.getenv("TS_STATE_B64", "")
        if render_key and render_srv and (b64_str != current_env):
            url = f"https://api.render.com/v1/services/{render_srv}/env-vars/TS_STATE_B64"
            req = urllib.request.Request(
                url,
                data=json.dumps({"value": b64_str}).encode("utf-8"),
                headers={"Authorization": f"Bearer {render_key}", "Content-Type": "application/json"},
                method="PUT"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                print(f"[Tailscale Guardian] Synced TS_STATE_B64 to Render: HTTP {resp.status}")
                os.environ["TS_STATE_B64"] = b64_str
                return True
    except Exception as e:
        print(f"[Tailscale Guardian] State sync error: {e}")
    return False

def run_tailscale_up(force_key: Optional[str] = None, reset: bool = False):
    authkey = force_key or os.getenv("TAILSCALE_AUTHKEY", "")
    hostname = os.getenv("TAILSCALE_HOSTNAME", "render-weighbridge")
    if not authkey:
        return {"status": "error", "message": "TAILSCALE_AUTHKEY not set"}
    try:
        cmd = ["tailscale", "up", f"--authkey={authkey}", f"--hostname={hostname}", "--accept-routes"]
        if reset or not os.path.exists("/var/lib/tailscale/tailscaled.state"):
            cmd.append("--reset")
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=20).decode()
        sync_tailscale_state_to_render()
        return {"status": "success", "output": out}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "output": e.output.decode() if e.output else str(e)}
    except Exception as e:
        return {"status": "exception", "error": str(e)}

async def tailscale_guardian_loop():
    """Background task to ensure Tailscale stays connected and persists session node state."""
    await asyncio.sleep(15)  # Allow container to settle and load persistent session
    logged_out_consecutive = 0
    while True:
        try:
            status_res = await asyncio.to_thread(
                subprocess.run, ["tailscale", "status"], capture_output=True, text=True, timeout=5
            )
            output = (status_res.stdout or "") + (status_res.stderr or "")
            if "Logged out" in output or status_res.returncode != 0:
                logged_out_consecutive += 1
                if logged_out_consecutive >= 3:
                    print("[Tailscale Guardian] Tailscale logged out for 3 checks. Reconnecting...")
                    await asyncio.to_thread(run_tailscale_up, None, False)
                    logged_out_consecutive = 0
            else:
                logged_out_consecutive = 0
                if not os.getenv("TS_STATE_SYNCED"):
                    if await asyncio.to_thread(sync_tailscale_state_to_render):
                        os.environ["TS_STATE_SYNCED"] = "1"
        except Exception as e:
            print(f"[Tailscale Guardian] Loop error: {e}")
        await asyncio.sleep(30)

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_cloud_db()
    except Exception as e:
        print(f"Error initializing cloud DB: {e}")
    
    guardian_task = asyncio.create_task(tailscale_guardian_loop())
    yield
    guardian_task.cancel()

app = FastAPI(title="Weighbridge Cloud Portal", lifespan=lifespan)

# Mount static files and templates
STATIC_DIR = CLOUD_DIR / "static"
TEMPLATES_DIR = CLOUD_DIR / "templates"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["ist"] = format_ist

# Pydantic schema for ticket creation
class TicketCreateRequest(BaseModel):
    ticket_no: int
    vehicle_no: str
    customer_name: Optional[str] = ""
    mobile: Optional[str] = ""
    vehicle_type: Optional[str] = "🚚 Lorry / லாரி"
    load_status: Optional[str] = "Loaded / லோடு"
    material: Optional[str] = "General Cargo / பொதுச் சரக்கு"
    gross_weight: float = 0.0
    tare_weight: float = 0.0
    net_weight: float = 0.0
    first_weight: Optional[float] = None
    stage: Optional[str] = "COMPLETED"  # "FIRST_WEIGHT" or "COMPLETED"
    charge: float = 100.0
    copies: int = 1

DEFAULT_MATERIALS = [
    "General Cargo / பொதுச் சரக்கு",
    "Sand / மணல்",
    "Cotton / பருத்தி",
    "Cement / சிமென்ட்",
    "Maize / மக்காச்சோளம்",
    "Paddy / நெல்",
    "Gravel / ஜல்லி",
    "Iron Scrap / இரும்பு கழிவு",
    "Coconut / தேங்காய்",
    "Timber / மரம்",
    "Sugarcane / கரும்பு"
]

DEFAULT_VEHICLE_TYPES = [
    "🚚 Lorry / லாரி",
    "🚐 Tempo / டெம்போ",
    "🛺 Auto / ஆட்டோ",
    "🚜 Tractor / டிராக்டர்",
    "🚛 Tipper / டிப்பர்",
    "🚗 Car / Van / இதர"
]

# ---------------- Frontend Web Pages ----------------

def _verify_admin_access(
    key: Optional[str] = Query(None),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")
):
    provided = x_admin_key or key
    if not provided or provided != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized: Admin key required")
    return True

@app.get("/api/tailscale-status")
async def tailscale_status(
    key: Optional[str] = Query(None),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")
):
    try:
        res = await asyncio.to_thread(
            subprocess.run, ["tailscale", "status"], capture_output=True, text=True, timeout=5
        )
        out = res.stdout or res.stderr or ""
        if res.returncode != 0:
            return {"status": "cmd_error", "connected": False}
        state_file = "/var/lib/tailscale/tailscaled.state"
        payload = {
            "status": "ok",
            "connected": "Logged out" not in out,
            "has_state_file": os.path.exists(state_file),
            "has_env_state": bool(os.getenv("TS_STATE_B64"))
        }
        if (x_admin_key or key) == ADMIN_KEY:
            payload["output"] = out
        return payload
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/api/tailscale-up")
async def do_tailscale_up(
    authkey: Optional[str] = None,
    _auth: bool = Depends(_verify_admin_access)
):
    return await asyncio.to_thread(run_tailscale_up, authkey, False)

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request, session: Session = Depends(get_cloud_session)):
    next_no = get_next_cloud_ticket_number(session)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "active_page": "weigh",
            "business_name": BUSINESS_NAME,
            "business_capacity": BUSINESS_CAPACITY,
            "business_address": BUSINESS_ADDRESS,
            "business_phone": BUSINESS_PHONE,
            "next_ticket_no": next_no,
            "materials": DEFAULT_MATERIALS,
            "vehicle_types": DEFAULT_VEHICLE_TYPES,
            "default_charge": DEFAULT_CHARGE
        }
    )

@app.get("/tickets", response_class=HTMLResponse)
async def tickets_page(request: Request, session: Session = Depends(get_cloud_session)):
    statement = select(CloudTicket).order_by(desc(CloudTicket.id)).limit(100)
    tickets = session.exec(statement).all()
    return templates.TemplateResponse(
        request=request,
        name="tickets.html",
        context={
            "active_page": "tickets",
            "business_name": BUSINESS_NAME,
            "business_capacity": BUSINESS_CAPACITY,
            "business_phone": BUSINESS_PHONE,
            "tickets": tickets
        }
    )

@app.get("/slip/{ticket_id}", response_class=HTMLResponse)
async def view_slip(request: Request, ticket_id: int, auto_print: int = 0, session: Session = Depends(get_cloud_session)):
    ticket = session.get(CloudTicket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return templates.TemplateResponse(
        request=request,
        name="slip.html",
        context={
            "ticket": ticket,
            "business_name": BUSINESS_NAME,
            "business_capacity": BUSINESS_CAPACITY,
            "business_address": BUSINESS_ADDRESS,
            "business_phone": BUSINESS_PHONE,
            "auto_print": (auto_print == 1)
        }
    )

# ---------------- API Routes ----------------

@app.get("/api/live-weight")
async def api_live_weight():
    """
    Returns live scale reading via EdgeClient with 1-second in-memory SWR cache.
    Home PC experiences near-zero load.
    """
    return await edge_client.get_live_weight()

@app.get("/api/camera-snapshot")
async def api_camera_snapshot(camera: str = Query("front", pattern="^(front|rear|both)$")):
    """
    Fetches snapshot on-demand from Home PC via Tailscale Funnel.
    """
    img_bytes = await edge_client.get_camera_snapshot(camera=camera)
    if img_bytes:
        return Response(content=img_bytes, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})
    
    # Return 1x1 transparent GIF or placeholder if offline
    return Response(content=b"", media_type="image/jpeg", status_code=404)

@app.post("/api/gemini/detect")
async def api_gemini_detect():
    """
    Triggers Dual-Camera Fusion ANPR using Gemini AI directly in Cloud.
    Pulls front and rear frames on-demand in parallel from Home PC (no placeholders).
    """
    front_bytes, rear_bytes = await asyncio.gather(
        edge_client.get_camera_snapshot(camera="front", allow_placeholder=False),
        edge_client.get_camera_snapshot(camera="rear", allow_placeholder=False)
    )
    if not front_bytes and not rear_bytes:
        return {"success": False, "vehicle_no": "", "error": "Cameras offline / Home PC unreachable"}

    res = await gemini_cloud.detect_fusion(front_bytes=front_bytes, rear_bytes=rear_bytes)
    return res

@app.post("/api/gemini/upload")
async def api_gemini_upload(file: UploadFile = File(...)):
    """
    Scans an operator-uploaded vehicle photo using Gemini AI ANPR.
    """
    img_bytes = await file.read()
    if not img_bytes:
        raise HTTPException(status_code=400, detail="Empty file uploaded")
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 10 MB)")
    return await gemini_cloud.detect_fusion(front_bytes=img_bytes, rear_bytes=None)

@app.get("/api/vehicle-lookup")
async def api_vehicle_lookup(v: str = Query("", min_length=2), session: Session = Depends(get_cloud_session)):
    clean_v = v.upper().replace(" ", "").replace("-", "").strip()
    if not clean_v or len(clean_v) < 3:
        return {"found": False, "has_open": False}

    # 1. Check if there is an active open 1st-weight ticket in Cloud
    open_ticket = session.exec(
        select(CloudTicket)
        .where(CloudTicket.vehicle_no == clean_v, CloudTicket.status == "FIRST_WEIGHT")
        .order_by(desc(CloudTicket.id))
    ).first()

    if open_ticket:
        fw_val = open_ticket.first_weight or (open_ticket.gross_weight if open_ticket.gross_weight > 0 else open_ticket.tare_weight)
        return {
            "found": True,
            "has_open": True,
            "open_ticket": {
                "ticket_no": open_ticket.ticket_no,
                "first_weight": fw_val,
                "first_weight_at": format_ist(open_ticket.first_weight_at or open_ticket.created_at, "%d-%m-%Y %H:%M"),
                "customer_name": open_ticket.customer_name or "",
                "mobile": open_ticket.mobile or "",
                "vehicle_type": open_ticket.vehicle_type or "🚚 Lorry / லாரி",
                "load_status": open_ticket.load_status or "Loaded / லோடு",
                "material": open_ticket.material or "General Cargo / பொதுச் சரக்கு",
                "charge": open_ticket.charge
            }
        }

    # 2. Check for past completed tickets to retrieve stored tare
    past_ticket = session.exec(
        select(CloudTicket)
        .where(CloudTicket.vehicle_no == clean_v)
        .order_by(desc(CloudTicket.id))
    ).first()

    if past_ticket:
        tare_val = past_ticket.tare_weight if past_ticket.tare_weight > 0 else None
        return {
            "found": True,
            "has_open": False,
            "stored_tare": tare_val,
            "stored_tare_date": format_ist(past_ticket.created_at, "%d-%m-%Y"),
            "customer_name": past_ticket.customer_name or "",
            "mobile": past_ticket.mobile or "",
            "vehicle_type": past_ticket.vehicle_type or "🚚 Lorry / லாரி",
            "material": past_ticket.material or "General Cargo / பொதுச் சரக்கு"
        }

    return {"found": False, "has_open": False}

@app.post("/api/ticket/save-and-print")
async def api_ticket_save_and_print(
    req: TicketCreateRequest,
    request: Request,
    session: Session = Depends(get_cloud_session)
):
    """
    1. Saves or updates ticket in Cloud Database (First Weight or Completed).
    2. Renders A5 landscape slip HTML.
    3. Sends print payload to Home PC to physically print to Epson L3350.
    """
    clean_v = req.vehicle_no.upper().replace(" ", "").replace("-", "").strip()

    # If completing an open first weight ticket:
    existing_open = session.exec(
        select(CloudTicket)
        .where(CloudTicket.vehicle_no == clean_v, CloudTicket.status == "FIRST_WEIGHT")
        .order_by(desc(CloudTicket.id))
    ).first()

    is_stage_first = (req.stage == "FIRST_WEIGHT")

    if existing_open and not is_stage_first:
        ticket = existing_open
        ticket.customer_name = req.customer_name.strip()
        ticket.mobile = req.mobile.strip()
        ticket.vehicle_type = req.vehicle_type
        ticket.load_status = req.load_status
        ticket.material = req.material.strip()
        ticket.gross_weight = req.gross_weight
        ticket.tare_weight = req.tare_weight
        ticket.net_weight = req.net_weight
        ticket.charge = req.charge
        ticket.status = "COMPLETED"
        ticket.second_weight_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(ticket)
    else:
        ticket = None
        for _ in range(3):
            next_ticket_no = get_next_cloud_ticket_number(session)
            now_utc = datetime.now(timezone.utc)
            candidate = CloudTicket(
                ticket_no=next_ticket_no,
                vehicle_no=clean_v,
                customer_name=req.customer_name.strip(),
                mobile=req.mobile.strip(),
                vehicle_type=req.vehicle_type,
                load_status=req.load_status,
                material=req.material.strip(),
                gross_weight=req.gross_weight,
                tare_weight=req.tare_weight,
                net_weight=req.net_weight,
                first_weight=req.first_weight if is_stage_first else None,
                charge=req.charge,
                status="FIRST_WEIGHT" if is_stage_first else "COMPLETED",
                first_weight_at=now_utc if is_stage_first else None,
                second_weight_at=now_utc if not is_stage_first else None
            )
            session.add(candidate)
            try:
                session.commit()
                session.refresh(candidate)
                ticket = candidate
                break
            except IntegrityError:
                session.rollback()
        if not ticket:
            raise HTTPException(status_code=409, detail="Concurrent ticket number conflict; please retry.")

    # 2. Render slip HTML
    slip_html = templates.get_template("slip.html").render({
        "ticket": ticket,
        "business_name": BUSINESS_NAME,
        "business_capacity": BUSINESS_CAPACITY,
        "business_address": BUSINESS_ADDRESS,
        "business_phone": BUSINESS_PHONE,
        "auto_print": False
    })

    # 3. Dispatch print job to Home PC edge
    print_res = await edge_client.print_slip(
        ticket_no=ticket.ticket_no,
        html_content=slip_html,
        copies=req.copies
    )

    ticket.printed = print_res.get("success", False)
    if ticket.printed:
        ticket.printed_at = datetime.now(timezone.utc)
    session.add(ticket)
    session.commit()

    return {
        "success": True,
        "ticket_no": ticket.ticket_no,
        "status": ticket.status,
        "print_success": bool(ticket.printed),
        "print_status": print_res.get("message", "Print dispatched to Epson L3350")
    }

@app.post("/api/ticket/reprint")
async def api_ticket_reprint(ticket_no: int, session: Session = Depends(get_cloud_session)):
    statement = select(CloudTicket).where(CloudTicket.ticket_no == ticket_no)
    ticket = session.exec(statement).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    slip_html = templates.get_template("slip.html").render({
        "ticket": ticket,
        "business_name": BUSINESS_NAME,
        "business_capacity": BUSINESS_CAPACITY,
        "business_address": BUSINESS_ADDRESS,
        "business_phone": BUSINESS_PHONE,
        "auto_print": False
    })

    print_res = await edge_client.print_slip(
        ticket_no=ticket.ticket_no,
        html_content=slip_html,
        copies=1
    )
    return print_res
