from datetime import datetime, timezone
from typing import Optional, List
from sqlmodel import SQLModel, Field, Session, create_engine, select, func

try:
    from cloud_app.config import DATABASE_URL
except ImportError:
    from config import DATABASE_URL

# Handle SQLite vs Postgres connection args
connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)

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

def get_cloud_session():
    with Session(engine) as session:
        yield session

def get_next_cloud_ticket_number(session: Session) -> int:
    statement = select(func.max(CloudTicket.ticket_no))
    max_no = session.exec(statement).first()
    return (max_no or 1000) + 1
