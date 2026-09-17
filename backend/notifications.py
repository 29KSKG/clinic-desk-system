"""A stand-in Notification Service.

In production this would call an SMS/WhatsApp/email provider over HTTP. No such provider
exists in this environment, so ClinicDesk implements the same shape a real integration
would sit behind — one ``send`` call per message — and records everything it "sends" to
the ``outbox`` table. GET /api/outbox is the auditable delivery record a real provider's
dashboard would give you, and is what confirms the morning reminder job (T1) actually ran.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from . import clock
from .models import Appointment, Outbox, Patient


def send(
    db: Session,
    patient: Patient,
    kind: str,
    message: str,
    appointment: Optional[Appointment] = None,
    channel: str = "sms",
) -> Outbox:
    entry = Outbox(
        patient_id=patient.id,
        appointment_id=appointment.id if appointment else None,
        kind=kind,
        channel=channel,
        message=message,
        sent_at=clock.now(db),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
