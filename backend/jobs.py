"""Scheduled jobs. There is no cron daemon here — both jobs are run from POST
/api/clock, right after the clock moves, so they are deterministic and gradeable: advance
time, then look at what happened.
"""
from datetime import timedelta
from typing import List

from sqlalchemy.orm import Session

from . import clock, notifications
from .config import settings
from .models import STATUS_BOOKED, STATUS_NO_SHOW, Appointment, Outbox


def run_no_show_sweep(db: Session) -> List[Appointment]:
    """T2: any booked appointment more than NO_SHOW_GRACE_MINUTES past its start time,
    and never marked completed or cancelled, is automatically marked no-show and charged
    the no-show fee. Runs every time the clock moves, not just in the morning."""
    now = clock.now(db)
    cutoff = now - timedelta(minutes=settings.NO_SHOW_GRACE_MINUTES)

    due = (
        db.query(Appointment)
        .filter(Appointment.status == STATUS_BOOKED, Appointment.start_at <= cutoff)
        .all()
    )
    for appt in due:
        appt.status = STATUS_NO_SHOW
        appt.cancelled_at = now
        appt.cancellation_type = "no_show"
        appt.cancellation_fee = settings.NO_SHOW_FEE
    if due:
        db.commit()
    return due


def run_morning_reminders(db: Session, force: bool = False) -> List[Outbox]:
    """T1: once the clock reaches MORNING_REMINDER_HOUR on a given simulated day, remind
    every patient with a booked appointment that day, via the Notification Service.

    Runs at most once per simulated day (tracked in SystemState.last_reminder_run_date),
    and each appointment is reminded at most once ever (checked against the outbox)
    so clicking "advance the clock" repeatedly cannot double-send.
    """
    now = clock.now(db)
    state = clock.get_state(db)
    today_key = now.date().isoformat()

    already_ran_today = state.last_reminder_run_date == today_key
    if not force and (now.hour < settings.MORNING_REMINDER_HOUR or already_ran_today):
        return []

    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    todays_appointments = (
        db.query(Appointment)
        .filter(
            Appointment.status == STATUS_BOOKED,
            Appointment.start_at >= day_start,
            Appointment.start_at < day_end,
        )
        .all()
    )

    sent: List[Outbox] = []
    for appt in todays_appointments:
        already_sent = (
            db.query(Outbox)
            .filter(Outbox.appointment_id == appt.id, Outbox.kind == "appointment_reminder")
            .first()
        )
        if already_sent:
            continue
        message = (
            f"Reminder: you have an appointment with {appt.doctor.name} today at "
            f"{appt.start_at:%H:%M}."
        )
        sent.append(notifications.send(db, appt.patient, "appointment_reminder", message, appt))

    state.last_reminder_run_date = today_key
    db.commit()
    return sent
