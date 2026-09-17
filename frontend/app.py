"""ClinicDesk - Streamlit front-desk UI over the FastAPI backend.

Navigation lives in a top bar. Which items appear depends on the session:
signed-out visitors see Home and Sign in only; everything else is gated and any
attempt to land on a gated page bounces back to sign-in.
"""
import os
import traceback
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import streamlit as st

API = os.getenv("API_BASE_URL", "http://localhost:8000")

st.set_page_config(
    page_title="ClinicDesk",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="collapsed",
)

PUBLIC_PAGES = {"home", "auth"}

NAV_SIGNED_OUT = [("home", "🏠 Home"), ("auth", "🔐 Sign in")]
NAV_SIGNED_IN = [
    ("home", "🏠 Home"),
    ("dashboard", "📊 Dashboard"),
    ("book", "➕ Book"),
    ("day", "📅 Doctor's day"),
    ("appointments", "🔎 Appointments"),
    ("patients", "👥 Patients"),
    ("doctors", "🩺 Doctors"),
    ("search", "🔍 Search"),
    ("clock", "🕐 Clock & jobs"),
]

STATUS_STYLE = {
    "booked": ("#0d9488", "Booked"),
    "completed": ("#2563eb", "Completed"),
    "cancelled": ("#b45309", "Cancelled"),
    "no_show": ("#be123c", "No-show"),
}


# ==========================================================================
# session bootstrap
# ==========================================================================
def init_state():
    defaults = {
        "page": "home",
        "token": None,
        "user": None,
        "auth_mode": "login",
        "prefill_email": "",
        "auth_hint": None,
        "last_error": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def signed_in() -> bool:
    return bool(st.session_state.get("token"))


def goto(page: str, toast: str | None = None, icon: str = "✅"):
    st.session_state["page"] = page
    if toast:
        st.toast(toast, icon=icon)
    st.rerun()


# ==========================================================================
# styling
# ==========================================================================
def inject_css():
    st.markdown(
        """
        <style>
          #MainMenu, footer {visibility: hidden;}
          .block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1250px;}

          .cd-brand {
            display:flex; align-items:center; gap:.75rem;
            padding:.85rem 1.15rem; border-radius:14px; margin-bottom:.6rem;
            background:linear-gradient(110deg,#0f766e 0%,#0891b2 60%,#0ea5e9 100%);
            color:#fff; box-shadow:0 6px 18px rgba(13,148,136,.22);
          }
          .cd-brand h1 {font-size:1.3rem; margin:0; font-weight:700; letter-spacing:.2px;}
          .cd-brand span.tag {
            margin-left:auto; font-size:.8rem; opacity:.92;
            background:rgba(255,255,255,.18); padding:.3rem .7rem; border-radius:999px;
          }

          div[data-testid="stHorizontalBlock"] .stButton > button {
            border-radius:999px; font-weight:600; font-size:.88rem;
            padding:.42rem .2rem; border:1px solid #d7e5e5;
          }
          div[data-testid="stHorizontalBlock"] .stButton > button:hover {
            border-color:#0d9488; color:#0d9488;
          }

          .cd-card {
            background:#fff; border:1px solid #e3ecec; border-radius:14px;
            padding:1.05rem 1.2rem; margin-bottom:.85rem;
            box-shadow:0 1px 3px rgba(15,23,42,.05);
          }
          .cd-card h4 {margin:0 0 .35rem 0; font-size:1rem; color:#0f766e;}
          .cd-card p {margin:0; color:#475569; font-size:.92rem; line-height:1.5;}

          .cd-badge {
            display:inline-block; padding:.16rem .6rem; border-radius:999px;
            font-size:.75rem; font-weight:700; color:#fff;
          }
          .cd-slot {
            display:inline-block; margin:.18rem .3rem .18rem 0; padding:.3rem .65rem;
            border-radius:8px; background:#ecfdf5; color:#065f46;
            border:1px solid #a7f3d0; font-size:.83rem; font-family:ui-monospace,monospace;
          }
          .cd-row {
            display:flex; align-items:center; gap:.8rem; padding:.55rem .8rem;
            border-left:4px solid #0d9488; background:#fff; border-radius:8px;
            margin-bottom:.35rem; border-top:1px solid #eef2f2;
            border-right:1px solid #eef2f2; border-bottom:1px solid #eef2f2;
          }
          .cd-row .time {font-family:ui-monospace,monospace; font-weight:700; color:#0f766e;}
          .cd-row .who {font-weight:600;}
          .cd-row .meta {margin-left:auto; color:#64748b; font-size:.85rem;}
          .cd-muted {color:#64748b; font-size:.88rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def badge(status: str) -> str:
    colour, label = STATUS_STYLE.get(status, ("#64748b", status))
    return f'<span class="cd-badge" style="background:{colour}">{label}</span>'


# ==========================================================================
# API helper - every network call funnels through here
# ==========================================================================
def api(method: str, path: str, **kwargs):
    """Returns (ok: bool, payload: dict). Never raises."""
    headers = kwargs.pop("headers", {})
    if st.session_state.get("token"):
        headers["Authorization"] = f"Bearer {st.session_state['token']}"
    try:
        resp = requests.request(method, f"{API}{path}", headers=headers, timeout=20, **kwargs)
    except requests.Timeout:
        return False, {"message": "The API took too long to respond. Try again."}
    except requests.ConnectionError:
        return False, {
            "message": f"Cannot reach the API at {API}.",
            "hint": "Is uvicorn running? In Codespaces, make port 8000 public and set API_BASE_URL.",
        }
    except requests.RequestException as exc:
        return False, {"message": f"Request failed: {exc}"}

    if resp.status_code == 401 and not path.startswith("/api/auth/"):
        st.session_state["token"] = None
        st.session_state["user"] = None
        return False, {"message": "Your session expired. Please sign in again.", "expired": True}

    if resp.ok:
        try:
            return True, (resp.json() if resp.content else {})
        except ValueError:
            return False, {"message": "The API returned a response that could not be read."}

    try:
        detail = resp.json().get("detail", resp.text)
    except ValueError:
        detail = resp.text or f"HTTP {resp.status_code}"

    if isinstance(detail, dict):
        return False, detail
    if isinstance(detail, list):  # pydantic validation errors
        readable = "; ".join(
            f"{'.'.join(str(x) for x in e.get('loc', [])[1:])}: {e.get('msg')}" for e in detail
        )
        return False, {"message": readable or "Invalid input"}
    return False, {"message": str(detail), "status_code": resp.status_code}


def show_error(payload: dict):
    st.error(payload.get("message", "Something went wrong."), icon="🚫")
    if payload.get("hint"):
        st.caption(payload["hint"])
    if payload.get("conflicting_appointment_id"):
        st.info(
            f"Clashing appointment **#{payload['conflicting_appointment_id']}**"
            + (f" — {payload['conflicting_patient']}" if payload.get("conflicting_patient") else "")
            + ". Pick a different slot, or reschedule the existing one.",
            icon="📌",
        )


def fmt(value: str, pattern: str = "%d %b %Y, %H:%M") -> str:
    try:
        return datetime.fromisoformat(value).strftime(pattern)
    except (TypeError, ValueError):
        return str(value)


@st.cache_data(ttl=5, show_spinner=False)
def clinic_today(_token: str) -> date:
    """The clinic's current date — reads the simulated clock if one is set, else the real
    date. Short TTL so it stays in sync while someone is exercising the Clock & jobs page,
    without hitting the API on every widget interaction."""
    ok, info = api("GET", "/api/clock")
    if ok:
        return datetime.fromisoformat(info["now"]).date()
    return date.today()


@st.cache_data(ttl=30, show_spinner=False)
def cached_doctors(_token: str):
    ok, data = api("GET", "/api/doctors", params={"page_size": 100, "sort_by": "name"})
    return data.get("items", []) if ok else []


def doctor_list():
    doctors = cached_doctors(st.session_state["token"])
    if not doctors:
        st.info("No doctors on file yet. Add one on the **Doctors** page first.", icon="🩺")
    return doctors


def pager(data: dict, key: str):
    """Previous / next controls driven by the API's pagination envelope."""
    total_pages = max(1, data.get("total_pages", 1))
    current = data.get("page", 1)
    left, mid, right = st.columns([1, 3, 1])
    if left.button("← Previous", disabled=current <= 1, key=f"{key}_prev", use_container_width=True):
        st.session_state[key] = current - 1
        st.rerun()
    mid.markdown(
        f"<div style='text-align:center;padding-top:.45rem' class='cd-muted'>"
        f"Page <b>{current}</b> of <b>{total_pages}</b> · {data.get('total', 0)} result(s)</div>",
        unsafe_allow_html=True,
    )
    if right.button("Next →", disabled=current >= total_pages, key=f"{key}_next", use_container_width=True):
        st.session_state[key] = current + 1
        st.rerun()


# ==========================================================================
# top bar
# ==========================================================================
def top_bar():
    user = st.session_state.get("user")
    tag = f"{user['full_name']} · {user['role']}" if user else "Not signed in"
    st.markdown(
        f"""
        <div class="cd-brand">
          <h1>🩺 ClinicDesk</h1>
          <span class="tag">{tag}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    items = NAV_SIGNED_IN if signed_in() else NAV_SIGNED_OUT
    columns = st.columns(len(items) + (1 if signed_in() else 0))

    for col, (key, label) in zip(columns, items):
        active = st.session_state["page"] == key
        if col.button(
            label,
            key=f"nav_{key}",
            use_container_width=True,
            type="primary" if active else "secondary",
        ):
            goto(key)

    if signed_in():
        with columns[-1].popover("👤 Account", use_container_width=True):
            st.write(f"**{user['full_name']}**")
            st.caption(user["email"])
            ok, stats = api("GET", "/api/stats/today")
            if ok:
                st.caption(
                    f"Today: {stats['booked']} booked · {stats['cancelled']} cancelled · "
                    f"fees {stats['currency']} {stats['late_cancel_fees']:.0f}"
                )
            if st.button("Sign out", use_container_width=True):
                st.session_state["token"] = None
                st.session_state["user"] = None
                st.cache_data.clear()
                goto("home", "Signed out", "👋")
    st.write("")


# ==========================================================================
# pages
# ==========================================================================
def page_home():
    st.markdown(
        """
        <div style="padding:38px 34px;border-radius:18px;margin-bottom:1.2rem;
                    background:linear-gradient(120deg,#0f766e 0%,#0891b2 55%,#0ea5e9 100%);
                    color:#fff">
          <div style="font-size:.85rem;letter-spacing:.18em;text-transform:uppercase;opacity:.85">
            Front-desk scheduling
          </div>
          <h1 style="margin:.35rem 0 0;font-size:2.5rem;line-height:1.15">
            No doctor double-booked. Ever.
          </h1>
          <p style="font-size:1.12rem;margin:.8rem 0 0;max-width:780px;opacity:.95">
            ClinicDesk is the booking system for a multi-doctor clinic reception. Overlaps are
            refused at the moment of booking, the same slot can never be sold twice, and late
            cancellations are charged by one consistent rule instead of a judgement call.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not signed_in():
        cta, note = st.columns([1, 3])
        if cta.button("Sign in to the desk →", type="primary", use_container_width=True):
            goto("auth")
        note.markdown(
            "<div class='cd-muted' style='padding-top:.55rem'>"
            "Demo account after seeding: <code>desk@clinic.com</code> / <code>desk1234</code>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.subheader("What it is")
    st.write(
        "A replacement for the paper diary and the shared spreadsheet. Every booking passes "
        "through one rule engine, so a clash is caught at the counter instead of being "
        "discovered in the waiting room."
    )

    st.subheader("Key features")
    c1, c2, c3 = st.columns(3)
    features = [
        (c1, "🚫 Conflict-free booking",
         "Overlap is checked inside a locked transaction and backed by a unique database "
         "index, so two clerks clicking at once cannot both win."),
        (c1, "📅 Doctor's day view",
         "One screen per doctor per day: booked, cancelled, free slots and utilisation."),
        (c2, "💸 Fair cancellations",
         "Enough notice and it is free; later it carries a flat fee, quoted to the patient "
         "before anything is confirmed."),
        (c2, "🔎 Instant lookup",
         "Find an appointment by patient name, phone or doctor, with pagination and sorting "
         "on every list."),
        (c3, "🔐 Staff accounts",
         "Registration, login and JWT-protected APIs; every booking records who made it."),
        (c3, "🔁 Safe rescheduling",
         "Moving an appointment re-runs every conflict check against the new time."),
    ]
    for col, title, body in features:
        col.markdown(f"<div class='cd-card'><h4>{title}</h4><p>{body}</p></div>",
                     unsafe_allow_html=True)

    left, right = st.columns(2)
    with left:
        st.subheader("Who it is for")
        st.write(
            "Front-desk staff at small and mid-sized multi-doctor clinics, polyclinics and "
            "dental or physiotherapy practices — anywhere a handful of doctors share one "
            "reception."
        )
        st.subheader("How it helps")
        st.markdown(
            "- Removes double-booking, so nobody is turned away after arriving.\n"
            "- Makes the cancellation policy consistent across every clerk.\n"
            "- Answers *\"when is my appointment?\"* in seconds.\n"
            "- Leaves an audit trail: who booked what, when it was cancelled, what was charged."
        )
    with right:
        st.subheader("Three features we would build next")
        st.markdown(
            "1. **Reminders** over SMS/WhatsApp 24 hours ahead, with a one-tap cancel link "
            "that applies the same fee rule.\n"
            "2. **Waitlist auto-fill** — when a slot frees up, offer it to the next waiting "
            "patient automatically.\n"
            "3. **Recurring appointments and doctor leave** — block holidays and generate "
            "weekly follow-up series in one action."
        )
    st.divider()
    st.caption(f"API: {API} · Swagger docs at {API}/docs")


def page_auth():
    if signed_in():
        goto("dashboard")

    left, right = st.columns([1.1, 1])

    with left:
        hint = st.session_state.get("auth_hint")
        if hint:
            st.info(hint["text"], icon=hint.get("icon", "ℹ️"))
            st.session_state["auth_hint"] = None

        if st.session_state["auth_mode"] == "login":
            st.subheader("Sign in")
            with st.form("login_form"):
                email = st.text_input("Email", value=st.session_state["prefill_email"])
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)

            if submitted:
                if not email or not password:
                    st.warning("Enter both your email and your password.", icon="⚠️")
                else:
                    with st.spinner("Signing in…"):
                        ok, data = api(
                            "POST", "/api/auth/login",
                            json={"email": email.strip(), "password": password},
                        )
                    if ok:
                        st.session_state["token"] = data["access_token"]
                        st.session_state["user"] = data["user"]
                        st.session_state["prefill_email"] = ""
                        st.cache_data.clear()
                        goto("dashboard", f"Welcome back, {data['user']['full_name']}", "👋")
                    else:
                        # Not in our records? Send them to register instead of a dead end.
                        found, check = api("GET", "/api/auth/check-email",
                                           params={"email": email.strip()})
                        if found and not check.get("exists"):
                            st.session_state["auth_mode"] = "register"
                            st.session_state["prefill_email"] = email.strip()
                            st.session_state["auth_hint"] = {
                                "text": f"No account found for **{email.strip()}** — "
                                        "create one below and you'll be signed straight in.",
                                "icon": "🆕",
                            }
                            st.rerun()
                        else:
                            show_error(data)

            st.caption("New here?")
            if st.button("Create an account →", use_container_width=True):
                st.session_state["auth_mode"] = "register"
                st.rerun()

        else:
            st.subheader("Create an account")
            with st.form("register_form"):
                full_name = st.text_input("Full name")
                email = st.text_input("Work email", value=st.session_state["prefill_email"])
                password = st.text_input("Password (min 6 characters)", type="password")
                confirm = st.text_input("Confirm password", type="password")
                submitted = st.form_submit_button("Create account", type="primary",
                                                  use_container_width=True)

            if submitted:
                if not full_name or not email or not password:
                    st.warning("Fill in every field.", icon="⚠️")
                elif password != confirm:
                    st.warning("The two passwords don't match.", icon="⚠️")
                elif len(password) < 6:
                    st.warning("Use at least 6 characters.", icon="⚠️")
                else:
                    with st.spinner("Creating your account…"):
                        ok, data = api(
                            "POST", "/api/auth/register",
                            json={"full_name": full_name.strip(), "email": email.strip(),
                                  "password": password},
                        )
                    if ok:
                        # Registered: sign them in immediately, no second form to fill.
                        ok2, token_data = api(
                            "POST", "/api/auth/login",
                            json={"email": email.strip(), "password": password},
                        )
                        if ok2:
                            st.session_state["token"] = token_data["access_token"]
                            st.session_state["user"] = token_data["user"]
                            st.session_state["prefill_email"] = ""
                            goto("dashboard", "Account created — you're in", "🎉")
                        else:
                            st.session_state["auth_mode"] = "login"
                            st.session_state["prefill_email"] = email.strip()
                            st.session_state["auth_hint"] = {
                                "text": "Account created. Sign in to continue.", "icon": "✅"}
                            st.rerun()
                    elif data.get("status_code") == 409 or "already exists" in data.get("message", ""):
                        # Already registered: bounce to sign-in with the email filled in.
                        st.session_state["auth_mode"] = "login"
                        st.session_state["prefill_email"] = email.strip()
                        st.session_state["auth_hint"] = {
                            "text": f"**{email.strip()}** is already registered — sign in instead.",
                            "icon": "🔐",
                        }
                        st.rerun()
                    else:
                        show_error(data)

            st.caption("Already have an account?")
            if st.button("← Back to sign in", use_container_width=True):
                st.session_state["auth_mode"] = "login"
                st.rerun()

    with right:
        st.markdown(
            "<div class='cd-card'><h4>Why sign in?</h4><p>Every booking records who made it, "
            "and the API refuses unauthenticated requests. The desk pages unlock as soon as "
            "you're in.</p></div>",
            unsafe_allow_html=True,
        )
        ok, pol = api("GET", "/api/policy")
        if ok:
            st.markdown(
                f"<div class='cd-card'><h4>Cancellation policy</h4><p>Free up to "
                f"<b>{pol['free_cancel_hours']:.0f} hours</b> before the appointment. Later "
                f"cancellations cost <b>{pol['currency']} {pol['late_cancel_fee']:.0f}</b>; "
                f"a no-show costs <b>{pol['currency']} {pol['no_show_fee']:.0f}</b>.</p></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div class='cd-card'><h4>API offline</h4><p>The backend isn't answering yet. "
                "Start it with <code>uvicorn backend.main:app --reload --port 8000</code>."
                "</p></div>",
                unsafe_allow_html=True,
            )


def page_dashboard():
    st.subheader("Today at the desk")
    ok, stats = api("GET", "/api/stats/today")
    if not ok:
        show_error(stats)
        return

    if stats["doctors_active"] == 0 or stats["patients_total"] == 0:
        st.markdown(
            "<div class='cd-card' style='border-color:#0d9488;background:#f0fdfa'>"
            "<h4>👋 Let's get set up</h4><p>Add at least one doctor and register a patient "
            "before you can book anything. Both take under a minute.</p></div>",
            unsafe_allow_html=True,
        )
        oc1, oc2 = st.columns(2)
        if stats["doctors_active"] == 0 and oc1.button("🩺 Add a doctor", use_container_width=True,
                                                        type="primary"):
            goto("doctors")
        if stats["patients_total"] == 0 and oc2.button("👥 Register a patient",
                                                        use_container_width=True):
            goto("patients")
        st.divider()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Booked today", stats["booked"])
    c2.metric("Completed", stats["completed"])
    c3.metric("Cancelled", stats["cancelled"])
    c4.metric("No-shows", stats["no_show"])
    c5.metric(f"Fees ({stats['currency']})", f"{stats['late_cancel_fees']:.0f}")

    st.caption(f"{stats['doctors_active']} active doctors · {stats['patients_total']} patients on file")
    st.divider()

    left, right = st.columns([2, 1])
    with left:
        st.markdown("#### Today's schedule")
        today = clinic_today(st.session_state["token"])
        ok, data = api(
            "GET", "/api/appointments",
            params={"date_from": today.isoformat(), "date_to": today.isoformat(),
                    "page_size": 50, "sort_by": "start_at", "order": "asc"},
        )
        if not ok:
            show_error(data)
        elif not data["items"]:
            st.info("Nothing booked for today yet.", icon="📭")
        else:
            for a in data["items"]:
                st.markdown(
                    f"<div class='cd-row'>"
                    f"<span class='time'>{fmt(a['start_at'], '%H:%M')}</span>"
                    f"<span class='who'>{a['patient_name']}</span>"
                    f"<span class='cd-muted'>→ {a['doctor_name']}</span>"
                    f"<span class='meta'>{badge(a['status'])}</span></div>",
                    unsafe_allow_html=True,
                )
    with right:
        st.markdown("#### Quick actions")
        if st.button("➕ Book an appointment", use_container_width=True, type="primary"):
            goto("book")
        if st.button("🔎 Find a patient's appointment", use_container_width=True):
            goto("appointments")
        if st.button("📅 Open a doctor's day", use_container_width=True):
            goto("day")

        ok, pol = api("GET", "/api/policy")
        if ok:
            st.markdown(
                f"<div class='cd-card'><h4>Policy reminder</h4><p>Free cancellation up to "
                f"<b>{pol['free_cancel_hours']:.0f} h</b> before. Later: "
                f"<b>{pol['currency']} {pol['late_cancel_fee']:.0f}</b>.</p></div>",
                unsafe_allow_html=True,
            )


def page_book():
    st.subheader("Book an appointment")
    doctors = doctor_list()
    if not doctors:
        return

    step1, step2 = st.columns([1, 1])

    with step1:
        st.markdown("##### 1 · Doctor & time")
        doctor = st.selectbox("Doctor", doctors,
                              format_func=lambda d: f"{d['name']} · {d['specialty']}")
        c1, c2 = st.columns(2)
        today = clinic_today(st.session_state["token"])
        day = c1.date_input("Date", value=today, min_value=today)
        duration = c2.number_input("Duration (min)", min_value=5, max_value=240, step=5,
                                   value=int(doctor["slot_minutes"]))
        st.caption(
            f"Works {doctor['work_start']}–{doctor['work_end']} · "
            f"default slot {doctor['slot_minutes']} min"
            + (f" · room {doctor['room']}" if doctor.get("room") else "")
        )

        ok, slots = api(
            "GET", f"/api/doctors/{doctor['id']}/availability",
            params={"date": day.isoformat(), "duration_minutes": int(duration)},
        )
        if not ok:
            show_error(slots)
            return
        if not slots:
            st.warning("No free slots of that length on this day. Try another date, another "
                       "doctor, or a shorter appointment.", icon="📆")
            return

        st.markdown(
            "".join(f"<span class='cd-slot'>{fmt(s['start_at'], '%H:%M')}</span>" for s in slots[:24])
            + ("<span class='cd-muted'> …</span>" if len(slots) > 24 else ""),
            unsafe_allow_html=True,
        )
        slot = st.selectbox(
            "Choose a slot", slots,
            format_func=lambda s: f"{fmt(s['start_at'], '%H:%M')} – {fmt(s['end_at'], '%H:%M')}",
        )

    with step2:
        st.markdown("##### 2 · Patient")
        mode = st.radio("Patient", ["Existing", "New"], horizontal=True,
                        label_visibility="collapsed")
        patient = None

        if mode == "Existing":
            query = st.text_input("Search by name or phone", placeholder="e.g. Ananya or 98000")
            ok, page = api("GET", "/api/patients",
                           params={"q": query or None, "page_size": 25, "sort_by": "full_name"})
            if not ok:
                show_error(page)
                return
            options = page.get("items", [])
            if options:
                patient = st.selectbox("Patient", options,
                                       format_func=lambda p: f"{p['full_name']} · {p['phone']}")
            else:
                st.info("Nobody matched. Switch to **New** to register them.", icon="🔍")
        else:
            with st.form("new_patient_inline"):
                name = st.text_input("Full name")
                phone = st.text_input("Phone")
                email = st.text_input("Email (optional)")
                if st.form_submit_button("Register patient", use_container_width=True):
                    if not name or not phone:
                        st.warning("Name and phone are required.", icon="⚠️")
                    else:
                        body = {"full_name": name.strip(), "phone": phone.strip()}
                        if email:
                            body["email"] = email.strip()
                        ok, data = api("POST", "/api/patients", json=body)
                        if ok:
                            st.success(f"Registered {data['full_name']} (#{data['id']}). "
                                       "Switch to **Existing** to book them in.", icon="✅")
                        else:
                            show_error(data)

        reason = st.text_input("Reason (optional)", placeholder="Follow-up, fever, review…")

    st.divider()
    ready = patient is not None
    if not ready:
        st.caption("Select a patient to enable booking.")
    if st.button("Confirm booking", type="primary", disabled=not ready, use_container_width=True):
        with st.spinner("Checking the slot and booking…"):
            ok, data = api(
                "POST", "/api/appointments",
                json={"doctor_id": doctor["id"], "patient_id": patient["id"],
                      "start_at": slot["start_at"], "duration_minutes": int(duration),
                      "reason": reason or None},
            )
        if ok:
            st.success(
                f"**#{data['id']}** · {data['patient_name']} with {data['doctor_name']} on "
                f"{fmt(data['start_at'])}", icon="🎉",
            )
            st.balloons()
        else:
            show_error(data)


def page_day():
    st.subheader("Doctor's day")
    doctors = doctor_list()
    if not doctors:
        return

    c1, c2, c3 = st.columns([2, 1, 1.2])
    doctor = c1.selectbox("Doctor", doctors, format_func=lambda d: f"{d['name']} · {d['specialty']}")
    today = clinic_today(st.session_state["token"])
    quick = c3.radio("Quick pick", ["Today", "Tomorrow", "Pick a date"], horizontal=False)
    if quick == "Today":
        day = today
        c2.date_input("Date", value=today, disabled=True)
    elif quick == "Tomorrow":
        day = today + timedelta(days=1)
        c2.date_input("Date", value=day, disabled=True)
    else:
        day = c2.date_input("Date", value=today)

    ok, view = api("GET", f"/api/doctors/{doctor['id']}/day", params={"date": day.isoformat()})
    if not ok:
        show_error(view)
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Booked", len(view["booked"]))
    m2.metric("Cancelled", len(view["cancelled"]))
    m3.metric("Free slots", len(view["free_slots"]))
    m4.metric("Utilisation", f"{view['utilisation_percent']}%")
    st.progress(min(1.0, view["utilisation_percent"] / 100))

    left, right = st.columns([2, 1])
    with left:
        st.markdown("#### Schedule")
        if view["booked"]:
            for a in view["booked"]:
                st.markdown(
                    f"<div class='cd-row'>"
                    f"<span class='time'>{fmt(a['start_at'], '%H:%M')}–{fmt(a['end_at'], '%H:%M')}</span>"
                    f"<span class='who'>{a['patient_name']}</span>"
                    f"<span class='cd-muted'>{a['patient_phone']} · {a['reason'] or 'No reason given'}</span>"
                    f"<span class='meta'>{badge(a['status'])} &nbsp;#{a['id']}</span></div>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("Nothing booked on this day.", icon="📭")

        if view["cancelled"]:
            with st.expander(f"Cancelled / no-show ({len(view['cancelled'])})"):
                st.dataframe(
                    pd.DataFrame([
                        {"Time": fmt(a["start_at"], "%H:%M"), "Patient": a["patient_name"],
                         "Type": a["cancellation_type"] or "-",
                         "Fee": a["cancellation_fee"],
                         "Reason": a["cancellation_reason"] or "-"}
                        for a in view["cancelled"]
                    ]),
                    use_container_width=True, hide_index=True,
                )
    with right:
        st.markdown("#### Free slots")
        if view["free_slots"]:
            st.markdown(
                "".join(f"<span class='cd-slot'>{fmt(s['start_at'], '%H:%M')}</span>"
                        for s in view["free_slots"]),
                unsafe_allow_html=True,
            )
            if st.button("Book one of these →", use_container_width=True, type="primary"):
                goto("book")
        else:
            st.success("Fully booked.", icon="✅")
        st.caption(f"Working hours {view['work_start']}–{view['work_end']}")


def page_appointments():
    st.subheader("Find an appointment")

    c1, c2, c3 = st.columns([3, 1, 1])
    query = c1.text_input("Search by patient name, phone or doctor",
                          placeholder="e.g. Meera, 98000, Dr. Verma")
    status_filter = c2.selectbox("Status", ["all", "booked", "cancelled", "completed", "no_show"])
    page_size = c3.selectbox("Per page", [5, 10, 20, 50], index=1)

    c4, c5, c6 = st.columns(3)
    sort_by = c4.selectbox("Sort by", ["start_at", "created_at", "status", "id"])
    order = c5.selectbox("Order", ["asc", "desc"])
    date_range = c6.date_input("Date range (optional)", value=(), key="appt_range")

    st.session_state.setdefault("appt_page", 1)
    params = {
        "q": query or None,
        "page": st.session_state["appt_page"],
        "page_size": int(page_size),
        "sort_by": sort_by,
        "order": order,
    }
    if status_filter != "all":
        params["status"] = status_filter
    if isinstance(date_range, tuple) and len(date_range) == 2:
        params["date_from"] = date_range[0].isoformat()
        params["date_to"] = date_range[1].isoformat()

    ok, data = api("GET", "/api/appointments", params=params)
    if not ok:
        show_error(data)
        return

    if data["page"] > data["total_pages"]:
        st.session_state["appt_page"] = 1
        st.rerun()

    if not data["items"]:
        st.info("No appointments matched those filters.", icon="🔍")
        return

    st.dataframe(
        pd.DataFrame([
            {"ID": a["id"], "When": fmt(a["start_at"]),
             "Ends": fmt(a["end_at"], "%H:%M"),
             "Patient": a["patient_name"], "Phone": a["patient_phone"],
             "Doctor": a["doctor_name"],
             "Status": STATUS_STYLE.get(a["status"], ("", a["status"]))[1],
             "Fee": a["cancellation_fee"]}
            for a in data["items"]
        ]),
        use_container_width=True, hide_index=True,
        column_config={"Fee": st.column_config.NumberColumn(format="%.2f")},
    )
    pager(data, "appt_page")

    live = [a for a in data["items"] if a["status"] == "booked"]
    if not live:
        st.caption("No live appointments on this page to manage.")
        return

    st.divider()
    st.markdown("#### Manage an appointment")
    target = st.selectbox(
        "Appointment", live,
        format_func=lambda a: f"#{a['id']} · {a['patient_name']} · {fmt(a['start_at'])} · {a['doctor_name']}",
    )

    tab_cancel, tab_move, tab_close = st.tabs(["🚫 Cancel", "🔁 Reschedule", "✔️ Close out"])
    with tab_cancel:
        ok, quote = api("GET", f"/api/appointments/{target['id']}/cancellation-quote")
        if not ok:
            show_error(quote)
        else:
            if quote["kind"] == "free":
                st.success(
                    f"{quote['hours_notice']:.1f} h notice — **free cancellation** "
                    f"(policy: {quote['free_cancel_hours']:.0f} h).", icon="✅",
                )
            else:
                st.warning(
                    f"Only {quote['hours_notice']:.1f} h notice — a late fee of "
                    f"**{quote['currency']} {quote['fee']:.2f}** applies.", icon="💸",
                )
            reason = st.text_input("Reason (optional)", key=f"cancel_reason_{target['id']}")
            confirm = st.checkbox("I've told the patient about any fee",
                                  key=f"cancel_ack_{target['id']}")
            if st.button("Confirm cancellation", type="primary", disabled=not confirm):
                ok, result = api("POST", f"/api/appointments/{target['id']}/cancel",
                                 json={"reason": reason or None})
                if ok:
                    st.toast(f"Cancelled #{result['id']} ({result['cancellation_type']})", icon="🚫")
                    st.rerun()
                else:
                    show_error(result)

    with tab_move:
        st.caption(f"Stays with **{target['doctor_name']}** — only the time changes.")
        rs_today = clinic_today(st.session_state["token"])
        new_day = st.date_input("New date", value=rs_today, min_value=rs_today,
                                key=f"rs_day_{target['id']}")
        ok, slots = api("GET", f"/api/doctors/{target['doctor_id']}/availability",
                        params={"date": new_day.isoformat()})
        if not ok:
            show_error(slots)
        elif not slots:
            st.info("No free slots on that day for this doctor.", icon="📆")
        else:
            new_slot = st.selectbox("New slot", slots,
                                    format_func=lambda s: fmt(s["start_at"], "%H:%M"),
                                    key=f"rs_slot_{target['id']}")
            if st.button("Move appointment", type="primary"):
                ok, result = api(
                    "PATCH", f"/api/appointments/{target['id']}/reschedule",
                    json={"start_at": new_slot["start_at"]},
                )
                if ok:
                    st.toast(f"Moved to {fmt(result['start_at'])}", icon="🔁")
                    st.rerun()
                else:
                    show_error(result)

    with tab_close:
        cc1, cc2 = st.columns(2)
        if cc1.button("Mark completed", use_container_width=True):
            ok, result = api("POST", f"/api/appointments/{target['id']}/complete")
            if ok:
                st.toast("Marked completed", icon="✔️")
                st.rerun()
            else:
                show_error(result)
        if cc2.button("Mark no-show", use_container_width=True):
            ok, result = api("POST", f"/api/appointments/{target['id']}/no-show")
            if ok:
                st.toast(f"No-show recorded · fee {result['cancellation_fee']:.0f}", icon="🚷")
                st.rerun()
            else:
                show_error(result)


def page_patients():
    st.subheader("Patients")

    with st.expander("➕ Register a new patient"):
        with st.form("patient_form"):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Full name")
            phone = c2.text_input("Phone")
            email = c3.text_input("Email (optional)")
            notes = st.text_input("Notes (optional)")
            if st.form_submit_button("Save patient", type="primary"):
                if not name or not phone:
                    st.warning("Name and phone are required.", icon="⚠️")
                else:
                    body = {"full_name": name.strip(), "phone": phone.strip()}
                    if email:
                        body["email"] = email.strip()
                    if notes:
                        body["notes"] = notes
                    ok, data = api("POST", "/api/patients", json=body)
                    if ok:
                        st.toast(f"Added {data['full_name']}", icon="✅")
                    else:
                        show_error(data)

    c1, c2, c3 = st.columns([3, 1, 1])
    query = c1.text_input("Search patients", placeholder="Name, phone or email")
    sort_by = c2.selectbox("Sort by", ["full_name", "created_at", "phone", "id"])
    order = c3.selectbox("Order", ["asc", "desc"], key="pat_order")

    st.session_state.setdefault("pat_page", 1)
    ok, data = api("GET", "/api/patients",
                   params={"q": query or None, "page": st.session_state["pat_page"],
                           "page_size": 10, "sort_by": sort_by, "order": order})
    if not ok:
        show_error(data)
        return
    if not data["items"]:
        st.info("No patients matched.", icon="🔍")
        return

    st.dataframe(
        pd.DataFrame([
            {"ID": p["id"], "Name": p["full_name"], "Phone": p["phone"],
             "Email": p["email"] or "—", "Registered": fmt(p["created_at"], "%d %b %Y")}
            for p in data["items"]
        ]),
        use_container_width=True, hide_index=True,
    )
    pager(data, "pat_page")

    st.divider()
    st.markdown("#### Appointment history")
    chosen = st.selectbox("Patient", data["items"],
                          format_func=lambda p: f"{p['full_name']} · {p['phone']}")
    ok, hist = api("GET", "/api/appointments",
                   params={"patient_id": chosen["id"], "page_size": 50,
                           "sort_by": "start_at", "order": "desc"})
    if not ok:
        show_error(hist)
    elif hist["items"]:
        for a in hist["items"]:
            st.markdown(
                f"<div class='cd-row'><span class='time'>{fmt(a['start_at'])}</span>"
                f"<span class='who'>{a['doctor_name']}</span>"
                f"<span class='meta'>{badge(a['status'])}"
                + (f" &nbsp;<span class='cd-muted'>fee {a['cancellation_fee']:.0f}</span>"
                   if a["cancellation_fee"] else "")
                + "</span></div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("No appointments yet for this patient.")


def page_doctors():
    st.subheader("Doctors")

    with st.expander("➕ Add a doctor"):
        with st.form("doctor_form"):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Name")
            specialty = c2.text_input("Specialty", "General")
            room = c3.text_input("Room (optional)")
            c4, c5, c6 = st.columns(3)
            work_start = c4.text_input("Work start (HH:MM)", "09:00")
            work_end = c5.text_input("Work end (HH:MM)", "17:00")
            slot = c6.number_input("Slot minutes", min_value=5, max_value=240, value=30, step=5)
            if st.form_submit_button("Save doctor", type="primary"):
                if not name:
                    st.warning("A name is required.", icon="⚠️")
                else:
                    ok, data = api("POST", "/api/doctors",
                                   json={"name": name.strip(), "specialty": specialty or "General",
                                         "room": room or None, "work_start": work_start,
                                         "work_end": work_end, "slot_minutes": int(slot)})
                    if ok:
                        st.cache_data.clear()
                        st.toast(f"Added {data['name']}", icon="🩺")
                    else:
                        show_error(data)

    c1, c2, c3 = st.columns([3, 1, 1])
    query = c1.text_input("Search doctors", placeholder="Name, specialty or room")
    sort_by = c2.selectbox("Sort by", ["name", "specialty", "created_at", "id"])
    order = c3.selectbox("Order", ["asc", "desc"], key="doc_order")

    st.session_state.setdefault("doc_page", 1)
    ok, data = api("GET", "/api/doctors",
                   params={"q": query or None, "page": st.session_state["doc_page"],
                           "page_size": 10, "sort_by": sort_by, "order": order})
    if not ok:
        show_error(data)
        return
    if not data["items"]:
        st.info("No doctors matched.", icon="🔍")
        return

    st.dataframe(
        pd.DataFrame([
            {"ID": d["id"], "Name": d["name"], "Specialty": d["specialty"],
             "Room": d["room"] or "—", "Hours": f"{d['work_start']}–{d['work_end']}",
             "Slot (min)": d["slot_minutes"], "Active": d["is_active"]}
            for d in data["items"]
        ]),
        use_container_width=True, hide_index=True,
    )
    pager(data, "doc_page")

    st.divider()
    st.markdown("#### Update a doctor")
    chosen = st.selectbox("Doctor", data["items"], format_func=lambda d: d["name"])
    c1, c2, c3, c4 = st.columns(4)
    new_start = c1.text_input("Work start", chosen["work_start"], key="upd_start")
    new_end = c2.text_input("Work end", chosen["work_end"], key="upd_end")
    new_slot = c3.number_input("Slot minutes", 5, 240, int(chosen["slot_minutes"]), 5, key="upd_slot")
    active = c4.checkbox("Taking appointments", value=chosen["is_active"], key="upd_active")
    if st.button("Save changes", type="primary"):
        ok, result = api("PATCH", f"/api/doctors/{chosen['id']}",
                         json={"work_start": new_start, "work_end": new_end,
                               "slot_minutes": int(new_slot), "is_active": active})
        if ok:
            st.cache_data.clear()
            st.toast(f"Updated {result['name']}", icon="✅")
            st.rerun()
        else:
            show_error(result)


def page_search():
    st.subheader("Global search")
    query = st.text_input("Search patients, doctors and appointments",
                          placeholder="A name, a phone number, a specialty…")
    if not query:
        st.caption("Type at least one character to search across everything.")
        return

    ok, data = api("GET", "/api/search", params={"q": query, "limit": 10})
    if not ok:
        show_error(data)
        return

    hits = len(data["patients"]) + len(data["doctors"]) + len(data["appointments"])
    if hits == 0:
        st.info(f"Nothing matched “{query}”.", icon="🔍")
        return

    t1, t2, t3 = st.tabs([
        f"👥 Patients ({len(data['patients'])})",
        f"🩺 Doctors ({len(data['doctors'])})",
        f"📅 Appointments ({len(data['appointments'])})",
    ])
    with t1:
        for p in data["patients"]:
            st.markdown(
                f"<div class='cd-row'><span class='who'>{p['full_name']}</span>"
                f"<span class='cd-muted'>{p['phone']} · {p['email'] or 'no email'}</span>"
                f"<span class='meta'>#{p['id']}</span></div>", unsafe_allow_html=True)
    with t2:
        for d in data["doctors"]:
            st.markdown(
                f"<div class='cd-row'><span class='who'>{d['name']}</span>"
                f"<span class='cd-muted'>{d['specialty']} · {d['work_start']}–{d['work_end']}</span>"
                f"<span class='meta'>#{d['id']}</span></div>", unsafe_allow_html=True)
    with t3:
        for a in data["appointments"]:
            st.markdown(
                f"<div class='cd-row'><span class='time'>{fmt(a['start_at'])}</span>"
                f"<span class='who'>{a['patient_name']}</span>"
                f"<span class='cd-muted'>→ {a['doctor_name']}</span>"
                f"<span class='meta'>{badge(a['status'])}</span></div>", unsafe_allow_html=True)


def run_clock(body: dict):
    with st.spinner("Advancing the clinic clock…"):
        ok, result = api("POST", "/api/clock", json=body)
    if ok:
        clinic_today.clear()
        st.session_state["last_clock_result"] = result
        n_no_show = len(result["no_shows_marked"])
        n_reminders = len(result["reminders_sent"])
        parts = []
        if n_no_show:
            parts.append(f"{n_no_show} marked no-show")
        if n_reminders:
            parts.append(f"{n_reminders} reminder(s) sent")
        st.toast(f"Now {fmt(result['now'])}" + (f" — {', '.join(parts)}" if parts else ""),
                 icon="🕐")
        st.rerun()
    else:
        show_error(result)


def page_clock():
    st.subheader("Simulated clock & scheduled jobs")
    st.caption(
        "Drives two jobs deterministically instead of waiting in real time: the morning "
        "reminder run (sends via the Notification Service, logged to the outbox below) "
        "and the no-show sweep (auto-marks appointments no-show 30 min past their start)."
    )

    ok, info = api("GET", "/api/clock")
    if not ok:
        show_error(info)
        return

    c1, c2 = st.columns(2)
    c1.metric("Clinic time right now", fmt(info["now"]))
    c2.metric("Clock source", "Simulated" if info["is_simulated"] else "Real time")

    st.markdown("##### Advance time")
    b1, b2, b3, b4, b5 = st.columns(5)
    if b1.button("+30 min", use_container_width=True):
        run_clock({"advance_minutes": 30})
    if b2.button("+1 hour", use_container_width=True):
        run_clock({"advance_minutes": 60})
    if b3.button("+1 day", use_container_width=True):
        run_clock({"advance_hours": 24})
    if b4.button("Jump to tomorrow 8 AM", use_container_width=True):
        base = datetime.fromisoformat(info["now"])
        target = (base + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        run_clock({"now": target.isoformat()})
    if b5.button("Reset to real time", use_container_width=True):
        run_clock({"reset": True})

    with st.expander("Set an exact date & time"):
        base = datetime.fromisoformat(info["now"])
        d = st.date_input("Date", value=base.date(), key="clock_set_date")
        t = st.time_input("Time", value=base.time(), key="clock_set_time")
        if st.button("Set clock", type="primary"):
            run_clock({"now": datetime.combine(d, t).isoformat()})

    last = st.session_state.get("last_clock_result")
    if last:
        st.markdown("##### Last run")
        n1, n2 = st.columns(2)
        n1.metric("No-shows marked", len(last["no_shows_marked"]))
        n2.metric("Reminders sent", len(last["reminders_sent"]))
        if last["no_shows_marked"]:
            st.caption("Appointment IDs: " + ", ".join(f"#{i}" for i in last["no_shows_marked"]))

    st.divider()
    st.markdown("##### Outbox — what the Notification Service has sent")
    kind = st.selectbox("Filter by kind", ["all", "appointment_reminder"])
    ok, data = api("GET", "/api/outbox",
                   params={"page_size": 15, "kind": None if kind == "all" else kind})
    if not ok:
        show_error(data)
        return
    if not data["items"]:
        st.info("Nothing sent yet — advance the clock past 08:00 on a day with booked "
                "appointments to trigger the morning reminder job.", icon="📭")
        return
    for o in data["items"]:
        st.markdown(
            f"<div class='cd-row'><span class='time'>{fmt(o['sent_at'])}</span>"
            f"<span class='who'>{o['patient_name']}</span>"
            f"<span class='cd-muted'>{o['message']}</span>"
            f"<span class='meta'>{o['channel']} · {o['kind']}</span></div>",
            unsafe_allow_html=True,
        )


PAGES = {
    "home": page_home,
    "auth": page_auth,
    "dashboard": page_dashboard,
    "book": page_book,
    "day": page_day,
    "appointments": page_appointments,
    "patients": page_patients,
    "doctors": page_doctors,
    "search": page_search,
    "clock": page_clock,
}


# ==========================================================================
# router
# ==========================================================================
def main():
    init_state()
    inject_css()

    page = st.session_state["page"]
    if page not in PAGES:
        page = st.session_state["page"] = "home"

    # Gate: a signed-out visitor cannot land on a desk page, however they got there.
    if page not in PUBLIC_PAGES and not signed_in():
        st.session_state["page"] = "auth"
        st.session_state["auth_hint"] = {
            "text": "Please sign in to use the desk.", "icon": "🔒"}
        page = "auth"

    top_bar()

    try:
        PAGES[page]()
    except Exception as exc:  # last-resort guard so the desk never sees a raw traceback
        st.error("Something went wrong on this screen. Your data is safe.", icon="💥")
        st.caption(f"{type(exc).__name__}: {exc}")
        with st.expander("Technical details (for the developer)"):
            st.code(traceback.format_exc(), language="text")
        if st.button("Back to a safe page"):
            goto("dashboard" if signed_in() else "home")


main()
