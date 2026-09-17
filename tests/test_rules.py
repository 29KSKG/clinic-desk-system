"""Unit tests for the two rules that matter: no overlap, fair cancellation fee."""
from datetime import date, datetime, timedelta

from backend.scheduling import (
    cancellation_outcome,
    free_slots,
    overlaps,
    within_working_hours,
)


def dt(h, m=0, day=1):
    return datetime(2030, 1, day, h, m)


def test_touching_intervals_do_not_overlap():
    assert not overlaps(dt(9), dt(9, 30), dt(9, 30), dt(10))


def test_partial_overlap_detected_both_ways():
    assert overlaps(dt(9), dt(9, 30), dt(9, 15), dt(9, 45))
    assert overlaps(dt(9, 15), dt(9, 45), dt(9), dt(9, 30))


def test_containment_is_an_overlap():
    assert overlaps(dt(9), dt(11), dt(9, 30), dt(10))
    assert overlaps(dt(9, 30), dt(10), dt(9), dt(11))


def test_working_hours_boundaries():
    assert within_working_hours("09:00", "17:00", dt(9), dt(9, 30))
    assert within_working_hours("09:00", "17:00", dt(16, 30), dt(17))
    assert not within_working_hours("09:00", "17:00", dt(16, 45), dt(17, 15))
    assert not within_working_hours("09:00", "17:00", dt(8, 45), dt(9, 15))


def test_appointment_cannot_straddle_midnight():
    assert not within_working_hours("09:00", "23:59", datetime(2030, 1, 1, 23), datetime(2030, 1, 2, 1))


def test_cancellation_is_free_with_enough_notice():
    now = dt(9)
    outcome = cancellation_outcome(now + timedelta(hours=48), now, 24, 300)
    assert outcome.kind == "free" and outcome.fee == 0.0


def test_cancellation_exactly_at_the_cutoff_is_free():
    now = dt(9)
    outcome = cancellation_outcome(now + timedelta(hours=24), now, 24, 300)
    assert outcome.kind == "free"


def test_late_cancellation_charges_the_fee():
    now = dt(9)
    outcome = cancellation_outcome(now + timedelta(hours=2), now, 24, 300)
    assert outcome.kind == "late" and outcome.fee == 300


def test_cancelling_after_the_start_time_is_late():
    now = dt(12)
    outcome = cancellation_outcome(dt(9), now, 24, 300)
    assert outcome.kind == "late" and outcome.hours_notice < 0


def test_free_slots_skip_busy_windows():
    day = date(2030, 1, 1)
    busy = [(dt(9, 30), dt(10))]
    slots = free_slots(day, "09:00", "11:00", 30, busy)
    assert [s.strftime("%H:%M") for s in slots] == ["09:00", "10:00", "10:30"]


def test_free_slots_respect_a_longer_duration():
    day = date(2030, 1, 1)
    busy = [(dt(10), dt(10, 30))]
    slots = free_slots(day, "09:00", "11:00", 30, busy, duration_minutes=60)
    assert [s.strftime("%H:%M") for s in slots] == ["09:00"]
