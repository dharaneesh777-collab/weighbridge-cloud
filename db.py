import os
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Field, Session, create_engine, select, func

try:
    from cloud_app.config import DATABASE_URL
except ImportError:
    from config import DATABASE_URL

# Handle SQLite vs Postgres connection args (fall back to SQLite when Firestore is active)
_use_firestore = bool(
    os.getenv("FIREBASE_SERVICE_ACCOUNT_B64")
    or os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
)
_effective_db_url = "sqlite:///cloud_weighbridge.db" if _use_firestore else DATABASE_URL
connect_args = {"check_same_thread": False} if "sqlite" in _effective_db_url else {}
engine = create_engine(_effective_db_url, echo=False, connect_args=connect_args)

def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)

class CloudTicket(SQLModel, table=True):
    __tablename__ = "cloud_tickets"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_no: int = Field(index=True, unique=True)
    vehicle_no: str = Field(index=True)
    customer_name: Optional[str] = Field(default="")
    mobile: Optional[str] = Field(default="")
    vehicle_type: Optional[str] = Field(default="🚚 Lorry / லாரி")
    load_status: Optional[str] = Field(default="Loaded / லோடு")
    material: Optional[str] = Field(default="General Cargo")
    gross_weight: float = Field(default=0.0)
    tare_weight: float = Field(default=0.0)
    net_weight: float = Field(default=0.0)
    first_weight: Optional[float] = Field(default=None)
    charge: float = Field(default=100.0)
    status: str = Field(default="COMPLETED")  # FIRST_WEIGHT or COMPLETED
    created_at: datetime = Field(default_factory=get_utc_now)
    first_weight_at: Optional[datetime] = None
    second_weight_at: Optional[datetime] = None
    printed: bool = Field(default=False)
    printed_at: Optional[datetime] = None
    remarks: Optional[str] = Field(default="")

def init_cloud_db():
    SQLModel.metadata.create_all(engine)
    # Safely add any new columns to an existing cloud_tickets table
    try:
        inspector = inspect(engine)
        if "cloud_tickets" in inspector.get_table_names():
            existing = {col["name"] for col in inspector.get_columns("cloud_tickets")}
            migrations = {
                "mobile": "VARCHAR DEFAULT ''",
                "vehicle_type": "VARCHAR DEFAULT '🚚 Lorry / லாரி'",
                "load_status": "VARCHAR DEFAULT 'Loaded / லோடு'",
                "first_weight": "FLOAT",
                "first_weight_at": "TIMESTAMP",
                "second_weight_at": "TIMESTAMP",
                "printed": "BOOLEAN DEFAULT FALSE",
                "printed_at": "TIMESTAMP",
                "remarks": "VARCHAR DEFAULT ''"
            }
            with engine.begin() as conn:
                for col_name, col_def in migrations.items():
                    if col_name not in existing:
                        conn.execute(text(f"ALTER TABLE cloud_tickets ADD COLUMN {col_name} {col_def}"))
    except Exception as e:
        print(f"DB migration check warning: {e}")

def get_cloud_session():
    with Session(engine) as session:
        yield session

def get_next_cloud_ticket_number(session: Session) -> int:
    statement = select(func.max(CloudTicket.ticket_no))
    max_no = session.exec(statement).first()
    return (max_no or 1000) + 1
