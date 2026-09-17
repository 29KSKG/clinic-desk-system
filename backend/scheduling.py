"""Pure scheduling rules - no database, no framework. Easy to unit-test."""
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from typing import Iterable, List, Sequence, Tuple


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Half-open intervals: [09:00, 09:30) and [09:30, 10:00) do NOT overlap."""
    return a_start < b_end and a_end > b_start


def within_working_hours(work_start: str, work_end: str, start: datetime, end: datetime) -> bool:
    ws, we = parse_hhmm(work_start), parse_hhmm(work_end)
    if start.date() != end.date():
        return False  # an appointment may not straddle midnight
    return start.time() >= ws and end.time() <= we


@dataclass
class CancellationOutcome:
    kind: str           # "free" | "late"
    fee: float
    hours_notice: float


def cancellation_outcome(
    start_at: datetime,
    now: datetime,
    free_hours: float,
    late_fee: float,
) -> CancellationOutcome:
    """Free if cancelled at least `free_hours` before the appointment starts.

    Anything later - including after the appointment was due to start - is a late
    cancellation and carries the flat fee.
    """
    hours_notice = (start_at - now).total_seconds() / 3600.0
    if hours_notice >= free_hours:
        return CancellationOutcome("free", 0.0, round(hours_notice, 2))
    return CancellationOutcome("late", round(late_fee, 2), round(hours_notice, 2))


def free_slots(
    day: date_cls,
    work_start: str,
    work_end: str,
    slot_minutes: int,
    busy: Sequence[Tuple[datetime, datetime]],
    now: datetime | None = None,
    duration_minutes: int | None = None,
) -> List[datetime]:
    """Every slot start on `day` that fits `duration_minutes` without clashing."""
    duration = duration_minutes or slot_minutes
    cursor = datetime.combine(day, parse_hhmm(work_start))
    day_end = datetime.combine(day, parse_hhmm(work_end))
    step = timedelta(minutes=slot_minutes)
    length = timedelta(minutes=duration)

    out: List[datetime] = []
    while cursor + length <= day_end:
        slot_end = cursor + length
        if (now is None or cursor >= now) and not any(
            overlaps(cursor, slot_end, b_start, b_end) for b_start, b_end in busy
        ):
            out.append(cursor)
        cursor += step
    return out


def normalise(dt: datetime) -> datetime:
    """The whole system works in naive clinic-local time."""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.replace(second=0, microsecond=0)
