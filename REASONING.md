# REASONING

## Reading the brief

The storyline is a list of complaints, so I turned each complaint into an invariant the
system has to hold:

| Complaint | Invariant |
|---|---|
| "keeps double-booking a doctor" | No two live appointments for one doctor may overlap |
| "two patients grab the same slot" | Same invariant, plus the same slot cannot be sold twice under concurrency |
| "late cancellation should carry a small fee" | Cancellation is free above a notice threshold, a flat fee below it |
| "see a doctor's day" | A day view per doctor: booked, cancelled, free |
| "find a patient's appointment by name" | Search on patient name/phone, and on doctor |

The brief said to get **conflict-free booking and the cancellation rule right first**, so
that is what I built and tested first; the lookups, the UI polish and the extras came after.

## Data model

Four tables: `users` (who logs in — front-desk staff, not patients), `doctors`,
`patients`, `appointments`.

The important decision is how a booking is stored. I store an explicit
`start_at` / `end_at` pair rather than a "slot number", because real clinics have doctors
with different slot lengths (a 20-minute paediatrics slot, a 45-minute orthopaedics slot)
and appointments that occasionally run long. With intervals, one overlap rule covers every
case.

Intervals are **half-open**: `[start, end)`. That makes back-to-back appointments
(09:00–09:30 and 09:30–10:00) legal, which is what a clinic actually wants, and it makes
the overlap test a single expression:

```
a.start < b.end AND a.end > b.start
```

All datetimes are naive clinic-local. A single clinic in a single timezone doesn't benefit
from UTC conversion, and naive local time keeps "Dr. Menon works 09:00–17:00" trivially
comparable. Any timezone-aware input is converted to local and stripped at the edge
(`scheduling.normalise`), so the database never holds a mixture.

## The double-booking problem, properly

A naive implementation does "SELECT to check, then INSERT". Two clerks clicking *Confirm*
at the same instant both see an empty slot and both insert. That is exactly the bug the
front desk is complaining about, so a plain check wasn't good enough.

I used three layers:

1. **Application check** — `assert_no_clash` looks for any live appointment overlapping the
   requested window, for the doctor *and* for the patient. It returns 409 with the clashing
   appointment's details so the desk can tell the patient what to do instead.
2. **Transaction isolation** — pysqlite starts transactions lazily, which leaves the
   check-then-insert interleavable. In `database.py` I disable the driver's implicit
   transaction handling and emit `BEGIN IMMEDIATE` on every transaction, which takes the
   write lock before the check runs. The check and the insert are now one atomic unit.
3. **A database constraint** — a partial unique index on `(doctor_id, start_at)` where
   `status = 'booked'`. Even if layers 1 and 2 were bypassed (a second process, someone
   writing SQL by hand), the database physically refuses a duplicate slot. The endpoint
   catches `IntegrityError` and converts it to the same friendly 409.

A general "no overlapping interval" constraint can't be expressed in SQLite (Postgres could
do it with an exclusion constraint on a `tstzrange` — noted as the migration path), hence
the layered approach instead of relying on the database alone.

I also enforced the **patient** side of the rule. The brief only mentions doctors, but a
desk that books the same patient with two doctors at 10:00 has the same class of bug, and
the check is nearly free once the query exists.

Two more guards that came out of thinking about what the desk would actually do wrong:
bookings outside the doctor's working hours, and bookings in the past. Both return 422 with
a message that says which rule was broken.

## The cancellation rule

Policy: **free if cancelled at least `FREE_CANCEL_HOURS` before the start time, otherwise a
flat `LATE_CANCEL_FEE`.** Defaults are 24 hours and 300, both environment variables because
"good time" and "small fee" are clinic policy, not physics.

Decisions inside that:

- The cut-off is `>=`, so exactly 24 hours is free. Boundaries should favour the patient.
- Cancelling *after* the start time yields negative notice, which falls through the same
  comparison and is charged as late. No special case needed.
- The fee, the type (`free` / `late`) and the timestamp are **stored on the row**, not
  recomputed later. If the clinic raises the fee next month, last month's cancellations must
  not silently change — that is an accounting record.
- A separate `GET /cancellation-quote` returns what the fee *would* be without changing
  anything, so the desk can tell the patient "that will be 300" before confirming. This was
  the single most obviously-needed endpoint once I imagined the phone call.
- Cancelling an already-cancelled appointment is a 409, not a silent no-op.
- No-show is a sibling case with its own fee, since a patient who never arrives is different
  from one who called.

The rule lives in `scheduling.py` as a pure function with no database or framework imports,
which is what made it easy to test every boundary.

## API shape

REST over the four nouns, with the verbs the desk actually uses as sub-resources
(`/cancel`, `/complete`, `/no-show`, `/reschedule`). Reschedule re-runs the full validation
with the appointment itself excluded from the clash search — otherwise an appointment would
always conflict with itself.

Every list endpoint shares one envelope (`items`, `total`, `page`, `page_size`,
`total_pages`, `sort_by`, `order`) and one pair of helpers, so pagination and sorting behave
identically everywhere. Sort fields are whitelisted per resource rather than passed into the
ORM raw — a `sort_by` straight from the query string is an injection surface.

Auth is JWT bearer tokens. Passwords are hashed with PBKDF2-SHA256 (200k iterations) from
the standard library rather than bcrypt/passlib, deliberately: it removes a native-build
dependency that frequently breaks in a fresh Codespace, and the security properties are
adequate here.

## UI

Streamlit, because the value of this product is the rules, not the pixels, and Streamlit
gets a usable multi-screen desk UI over the API quickly. Eight screens: landing, sign in,
book, doctor's day, appointments (search + manage), patients, doctors, global search.

The booking screen only ever offers slots the API says are free, so the common path never
hits a conflict at all — but the 409 is still surfaced clearly when it happens, because two
clerks can be on that screen at once. That was the point of the whole exercise.

## Testing, and what it caught

I tested the rules as pure functions first (`tests/test_rules.py`), then the whole stack
through `TestClient` (`tests/test_api.py`), then by hand through the UI.

Things the tests caught or forced me to decide:

- **Back-to-back bookings were being rejected.** My first overlap check used `<=`/`>=`,
  which treats 09:30–10:00 as clashing with 09:00–09:30. The half-open test fixed it; there
  is now an explicit test asserting touching intervals are fine.
- **Slot generation ran past closing time.** The loop initially emitted a 16:45 start for a
  30-minute slot in a day ending at 17:00. The condition is now `cursor + length <= day_end`,
  so the *end* has to fit, not the start.
- **Today's past slots were offered.** The availability endpoint passes `now` only when the
  requested date is today, so past slots disappear without hiding anything on future dates.
- **The same patient could be booked with two doctors at once.** Found by writing the test,
  not by writing the code; added the patient-side clash check.
- **A cancelled appointment didn't free its slot.** Because the clash query filters on
  `status == 'booked'`, it does — but the unique index would have blocked the rebooking if I
  had made it unconditional. That is why the index is partial (`where status = 'booked'`).
- **Concurrency.** I reproduced the race by firing two bookings for the same slot from two
  processes; before `BEGIN IMMEDIATE` both occasionally succeeded, after it one gets 409.

## The twists

### T6 (reschedule)

The reschedule endpoint already existed; the twist tightened it. Previously it accepted
an optional `doctor_id`, letting the desk move an appointment to a different doctor. The
twist says the doctor and patient must stay the same, so I removed that field from
`AppointmentReschedule` entirely rather than just ignoring it — a field the API silently
drops is worse than one that was never offered. The endpoint re-runs the exact same
`validate_window` + `assert_no_clash` pair a fresh booking uses, with the appointment
excluded from its own clash search, so a reschedule can never park two appointments on
top of each other. The UI's Reschedule tab lost its doctor picker to match.

### T1 and T2 (scheduled jobs, graded via `/clock` and `/outbox`)

The brief names the graded endpoints (`POST /clock`, `/outbox`) but not their payload
shape, so I had to design a contract rather than fill one in. The decisions:

- **A single simulated clock, not per-job state.** `SystemState.simulated_now` is one row
  the whole API reads "now" from (`clock.now(db)`) — booking-in-the-past checks,
  cancellation notice, the doctor's day view, today's stats, and both jobs. That was a
  deliberate choice over giving `/clock` its own private timeline: a grader (or a demo)
  advancing the clock should see the *entire system* move forward consistently, not just
  the two graded jobs, otherwise "today" in the UI and "today" in the reminder job could
  silently disagree.
- **`POST /clock` accepts `now` (absolute), `advance_minutes`/`advance_hours` (relative),
  or `reset`**, and always re-runs both jobs after moving, returning which appointments
  were marked no-show and which outbox entries were created. An empty body just re-runs
  the jobs against the current time — useful for polling without moving anything.
- **The Notification Service is mocked as an interface + outbox**, not a real HTTP call,
  since no provider was specified. `notifications.send()` is the shape a real integration
  (Twilio, SendGrid, whatever) would sit behind; every call it makes is logged to the
  `outbox` table, and `GET /api/outbox` is the delivery record — this is what "graded via
  /outbox" reads.
- **Idempotency, both ways.** The no-show sweep is naturally idempotent (once an
  appointment is `no_show` it no longer matches the `booked` filter, so re-running it is a
  no-op). The reminder job isn't naturally idempotent — advancing the clock five times
  during the same morning must not send five reminders — so it's guarded twice: a
  `last_reminder_run_date` on `SystemState` stops the *job* re-running the same simulated
  day, and a per-appointment outbox lookup stops any individual appointment being reminded
  twice even if the guard were bypassed.
- **`MORNING_REMINDER_HOUR` (default 8) and `NO_SHOW_GRACE_MINUTES` (default 30, matching
  the twist's own number) are environment variables**, same reasoning as the cancellation
  policy — "each morning" and "30 min after" are clinic policy, not something to hardcode.
- **What I did *not* build**: real background scheduling (a cron thread, Celery beat).
  Everything runs synchronously inside the `POST /clock` request, which is exactly what
  "graded via POST /clock" implies — the harness drives time, the server reacts. A
  production version would replace `POST /clock` with an actual clock tick and keep the
  same two job functions.

Tests for all three live in `tests/test_api.py`: a reschedule onto an occupied slot is
refused and the doctor/patient are asserted unchanged; the no-show sweep is checked at
29 minutes (still booked) and 31 minutes (auto no-show, fee applied); the reminder job is
checked before 8 AM (nothing sent), right after crossing 8 AM (one reminder, visible in
`/outbox`), and again later the same day (no second reminder).

## The UI/UX pass

The first UI was functional but flat: a sidebar radio for navigation, and no gating — a
signed-out visitor could technically select a desk page and just get a wall of 401 errors
from every API call it made. That got reworked into what's in `frontend/app.py` now.

**Top navbar instead of a sidebar.** Signed out, the nav shows only Home and Sign in.
Signing in reveals the eight desk screens plus an Account popover (name, today's counters,
sign out). The active page is highlighted as a filled button. This is a deliberate
trade — a sidebar is more conventional for a multi-page Streamlit app, but a top bar reads
more like the front-desk software it's modelling and made the "what's available before vs
after sign-in" distinction visually obvious rather than just logically enforced.

**Gated routing, not just a hidden nav.** Hiding the buttons isn't a guard by itself — the
router checks the target page against a `PUBLIC_PAGES` set on every render and bounces
anyone signed-out attempting a desk page back to Sign in with an explanation, regardless of
how `st.session_state["page"]` got set. This matters because Streamlit's session state is
just a dict; without the check, a stale token or a manually-poked state could otherwise
leave the UI calling protected endpoints and rendering their 401s.

**Sign-in ↔ register redirect.** This needed one new backend endpoint —
`GET /api/auth/check-email` — because the UI has to distinguish "wrong password" from "no
account" to route correctly, and it can't tell those apart from a 401 alone. On a failed
sign-in the UI calls check-email: no account → switch to Register with the address
pre-filled and a banner explaining why; account exists → the error stands (wrong
password). Registering with an email that's already taken (409) bounces the other
direction, back to Sign in, email pre-filled. A successful registration logs the user in
immediately rather than making them fill a second form — there's no reason a fresh account
shouldn't land straight on the Dashboard.

**Error handling at three levels**, because a desk clerk mid-booking is the worst possible
moment to hand someone a raw stack trace. The `api()` helper distinguishes timeouts,
connection-refused (with a Codespaces-specific hint about `API_BASE_URL`), validation
errors, 409 conflicts (shown with the clashing appointment's details), and expired tokens
(cleared automatically so the next action re-prompts sign-in) — each gets its own message
rather than one generic "request failed". Every form validates before it submits (empty
required fields, password confirmation, minimum length) so bad input never reaches the API
at all. And the router wraps every page render in a try/except, so an unanticipated
exception shows a plain-language message with the traceback tucked into a collapsed
expander — for me to read later — instead of Streamlit's default crash screen.

**Visual design.** A small CSS block (`inject_css`) replaces Streamlit's defaults with a
teal/cyan brand gradient, pill-shaped nav buttons, card components for the landing page's
feature grid, colour-coded status badges (booked/completed/cancelled/no-show), and slot
chips for free-time display. None of this changes behaviour — it's there because a desk
tool that looks like a generic admin panel is harder to trust at a glance than one with a
few consistent visual conventions.

**A later polish pass** made the UI itself clock-aware: every date picker (Book, Doctor's
day, Reschedule, the Dashboard's "today" schedule) now reads the clinic's current date
through `clinic_today()`, which calls `GET /api/clock` with a 5-second cache, instead of
the browser's real `date.today()`. Without this, advancing the simulated clock on the
Clock & jobs page would move the backend forward while every date picker elsewhere stayed
anchored to the real date — fine for normal desk use, but confusing evidence when
demonstrating T1/T2. A short-TTL cache keeps this from adding a request per widget
interaction, and `run_clock()` explicitly clears it after every clock change so the UI
doesn't wait out the TTL to catch up. Alongside that: a first-run onboarding banner on the
Dashboard when the clinic has zero doctors or zero patients, since a debugger's first
click into a bare install otherwise lands on a dashboard with silently blank numbers and
no cue what to do next.

## What I would do differently with more time


Postgres with a `tsrange` exclusion constraint (one line of DDL replaces two of my three
layers), a proper migration tool instead of `create_all`, refresh tokens and role-based
permissions, audit logging on every status change, and a background job for reminders.
Three product features are listed on the landing page: reminders with one-tap cancel,
waitlist auto-fill, and recurring appointments plus doctor leave.