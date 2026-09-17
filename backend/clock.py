"""The clinic's simulated clock.

Every place in the system that needs "now" reads it from here instead of calling
``datetime.now()`` directly — booking-in-the-past checks, cancellation notice, the
doctor's day view, today's stats, and the two scheduled jobs (morning reminders, the
no-show sweep). That means POST /api/clock, which can set or advance this value, moves
the *whole* system forward consistently rather than only one feature.

When no override has been set, ``now()`` returns the real wall clock, so normal day-to-day
use of the app needs no special handling — the simulated clock only matters once someone
(the grading harness, or a demo) calls POST /api/clock.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .models import SystemState

_STATE_ID = 1


def get_state(db: Session) -> SystemState:
    state = db.get(SystemState, _STATE_ID)
    if state is None:
        state = SystemState(id=_STATE_ID, simulated_now=None, last_reminder_run_date=None)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def now(db: Session) -> datetime:
    state = get_state(db)
    return state.simulated_now or datetime.now()


def today(db: Session):
    return now(db).date()


def set_now(db: Session, value: datetime) -> datetime:
    state = get_state(db)
    state.simulated_now = value
    db.commit()
    return state.simulated_now


def advance(db: Session, **delta) -> datetime:
    """Move the clock forward relative to its current value (simulated, or real if unset)."""
    state = get_state(db)
    base = state.simulated_now or datetime.now()
    state.simulated_now = base + timedelta(**delta)
    db.commit()
    return state.simulated_now


def reset(db: Session) -> None:
    """Drop back to the real wall clock."""
    state = get_state(db)
    state.simulated_now = None
    db.commit()
