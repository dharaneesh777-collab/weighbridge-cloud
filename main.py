import os
import time
from datetime import datetime
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import Session, select, desc

try:
    from cloud_app.config import (
        CLOUD_DIR, BUSINESS_NAME, BUSINESS_CAPACITY,
        BUSINESS_ADDRESS, BUSINESS_PHONE, DEFAULT_CHARGE, DEFAULT_COPIES
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
        BUSINESS_ADDRESS, BUSINESS_PHONE, DEFAULT_CHARGE, DEFAULT_COPIES
    )
    from db import (
        init_cloud_db, get_cloud_session, CloudTicket,
        get_next_cloud_ticket_number
    )
    from edge_client import edge_client
    from gemini_cloud import gemini_cloud

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_cloud_db()
    except Exception as e:
        print(f"Error initializing cloud DB: {e}")
    yield

app = FastAPI(title="Weighbridge Cloud Portal", lifespan=lifespan)

# Mount static files and templates
STATIC_DIR = CLOUD_DIR / "static"
TEMPLATES_DIR = CLOUD_DIR / "templates"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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

@app.get("/api/tailscale-status")
def tailscale_status():
    import subprocess
    try:
        out = subprocess.check_output(["tailscale", "status"], stderr=subprocess.STDOUT, timeout=5).decode()
        return {"status": "ok", "output": out}
    except subprocess.CalledProcessError as e:
        return {"status": "cmd_error", "output": e.output.decode() if e.output else str(e)}
    except Exception as e:
        return {"status": "error", "error": str(e)}

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
    Pulls front and rear frames on-demand from Home PC.
    """
    front_bytes = await edge_client.get_camera_snapshot(camera="front")
    rear_bytes = await edge_client.get_camera_snapshot(camera="rear")

    res = await gemini_cloud.detect_fusion(front_bytes=front_bytes, rear_bytes=rear_bytes)
    return res

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
                "first_weight_at": open_ticket.created_at.strftime("%d-%m-%Y %H:%M") if open_ticket.created_at else "",
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
            "stored_tare_date": past_ticket.created_at.strftime("%d-%m-%Y") if past_ticket.created_at else "",
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
        from datetime import timezone
        ticket.second_weight_at = datetime.now(timezone.utc)
    else:
        from datetime import timezone
        ticket = CloudTicket(
            ticket_no=req.ticket_no,
            vehicle_no=clean_v,
            customer_name=req.customer_name.strip(),
            mobile=req.mobile.strip(),
            vehicle_type=req.vehicle_type,
            load_status=req.load_status,
            material=req.material.strip(),
            gross_weight=req.gross_weight,
            tare_weight=req.tare_weight,
            net_weight=req.net_weight,
            first_weight=req.first_weight,
            charge=req.charge,
            status="FIRST_WEIGHT" if is_stage_first else "COMPLETED",
            first_weight_at=datetime.now(timezone.utc) if is_stage_first else None,
            second_weight_at=datetime.now(timezone.utc) if not is_stage_first else None
        )
        session.add(ticket)

    session.commit()
    session.refresh(ticket)

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

    from datetime import timezone
    ticket.printed = print_res.get("success", False)
    if ticket.printed:
        ticket.printed_at = datetime.now(timezone.utc)
    session.add(ticket)
    session.commit()

    return {
        "success": True,
        "ticket_no": ticket.ticket_no,
        "status": ticket.status,
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
