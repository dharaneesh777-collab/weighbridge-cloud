import base64
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from cloud_app.db import CloudTicket
except ImportError:
    from db import CloudTicket


class FirestoreStore:
    """
    Google Cloud Firestore backend for Weighbridge Cloud Portal.
    Uses single-field queries only (no composite indexes required) and
    transactional counters for collision-free ticket numbers.
    """

    TICKETS_COL = "weighbridge_tickets"
    META_COL = "weighbridge_meta"
    COUNTER_DOC = "counters"

    def __init__(self):
        self._client = None

    def is_enabled(self) -> bool:
        return bool(
            os.getenv("FIREBASE_SERVICE_ACCOUNT_B64")
            or os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        )

    def _get_client(self):
        if self._client is not None:
            return self._client

        from google.cloud import firestore
        from google.oauth2 import service_account

        sa_b64 = os.getenv("FIREBASE_SERVICE_ACCOUNT_B64", "").strip()
        sa_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()

        if sa_b64:
            info = json.loads(base64.b64decode(sa_b64).decode("utf-8"))
        elif sa_json:
            info = json.loads(sa_json)
        else:
            info = None

        if info is not None:
            if isinstance(info.get("private_key"), str):
                info["private_key"] = info["private_key"].replace("\r\n", "\n").replace("\\n", "\n")
            creds = service_account.Credentials.from_service_account_info(info)
            self._client = firestore.Client(credentials=creds, project=info.get("project_id"))
        else:
            self._client = firestore.Client()
        return self._client

    @staticmethod
    def _to_ticket(doc_data: Dict[str, Any]) -> CloudTicket:
        tno = int(doc_data.get("ticket_no", 0))
        return CloudTicket(
            id=int(doc_data.get("id", tno)),
            ticket_no=tno,
            vehicle_no=doc_data.get("vehicle_no", ""),
            customer_name=doc_data.get("customer_name", ""),
            mobile=doc_data.get("mobile", ""),
            vehicle_type=doc_data.get("vehicle_type", "🚚 Lorry / லாரி"),
            load_status=doc_data.get("load_status", "Loaded / லோடு"),
            material=doc_data.get("material", "General Cargo"),
            gross_weight=float(doc_data.get("gross_weight", 0.0)),
            tare_weight=float(doc_data.get("tare_weight", 0.0)),
            net_weight=float(doc_data.get("net_weight", 0.0)),
            first_weight=doc_data.get("first_weight"),
            charge=float(doc_data.get("charge", 100.0)),
            status=doc_data.get("status", "COMPLETED"),
            created_at=doc_data.get("created_at") or datetime.now(timezone.utc),
            first_weight_at=doc_data.get("first_weight_at"),
            second_weight_at=doc_data.get("second_weight_at"),
            printed=bool(doc_data.get("printed", False)),
            printed_at=doc_data.get("printed_at"),
            remarks=doc_data.get("remarks", ""),
        )

    def init_db(self):
        client = self._get_client()
        counter_ref = client.collection(self.META_COL).document(self.COUNTER_DOC)
        snap = counter_ref.get()
        if not snap.exists:
            counter_ref.set({"last_ticket_no": 1000})

    def get_next_ticket_number(self) -> int:
        client = self._get_client()
        snap = client.collection(self.META_COL).document(self.COUNTER_DOC).get()
        if snap.exists:
            return int((snap.to_dict() or {}).get("last_ticket_no", 1000)) + 1
        return 1001

    def list_tickets(self, limit: int = 100) -> List[CloudTicket]:
        from google.cloud import firestore

        client = self._get_client()
        docs = (
            client.collection(self.TICKETS_COL)
            .order_by("ticket_no", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [self._to_ticket(d.to_dict() or {}) for d in docs]

    def get_ticket_by_id(self, ticket_id: int) -> Optional[CloudTicket]:
        client = self._get_client()
        snap = client.collection(self.TICKETS_COL).document(str(ticket_id)).get()
        if snap.exists:
            return self._to_ticket(snap.to_dict() or {})
        return None

    def _get_vehicle_tickets(self, clean_v: str) -> List[CloudTicket]:
        client = self._get_client()
        docs = client.collection(self.TICKETS_COL).where("vehicle_no", "==", clean_v).stream()
        tickets = [self._to_ticket(d.to_dict() or {}) for d in docs]
        tickets.sort(key=lambda t: t.ticket_no, reverse=True)
        return tickets

    def find_open_first_weight(self, clean_v: str) -> Optional[CloudTicket]:
        for t in self._get_vehicle_tickets(clean_v):
            if t.status == "FIRST_WEIGHT":
                return t
        return None

    def find_latest_by_vehicle(self, clean_v: str) -> Optional[CloudTicket]:
        tickets = self._get_vehicle_tickets(clean_v)
        return tickets[0] if tickets else None

    def create_ticket(self, payload: Dict[str, Any]) -> CloudTicket:
        from google.cloud import firestore

        client = self._get_client()
        counter_ref = client.collection(self.META_COL).document(self.COUNTER_DOC)

        @firestore.transactional
        def _txn(transaction):
            snap = counter_ref.get(transaction=transaction)
            last_no = int((snap.to_dict() or {}).get("last_ticket_no", 1000)) if snap.exists else 1000
            next_no = last_no + 1
            transaction.set(counter_ref, {"last_ticket_no": next_no}, merge=True)

            doc_data = dict(payload)
            doc_data["id"] = next_no
            doc_data["ticket_no"] = next_no
            ticket_ref = client.collection(self.TICKETS_COL).document(str(next_no))
            transaction.set(ticket_ref, doc_data)
            return doc_data

        transaction = client.transaction()
        created = _txn(transaction)
        return self._to_ticket(created)

    def update_ticket(self, ticket_no: int, updates: Dict[str, Any]) -> Optional[CloudTicket]:
        client = self._get_client()
        doc_ref = client.collection(self.TICKETS_COL).document(str(ticket_no))
        doc_ref.set(updates, merge=True)
        snap = doc_ref.get()
        if snap.exists:
            return self._to_ticket(snap.to_dict() or {})
        return None


firestore_store = FirestoreStore()
