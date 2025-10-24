# -*- coding: utf-8 -*-
# /home/frappeuser/frappe-bench/apps/flugninja_case_management/...
# flugninja_case_management/doctype/flight/fetch_refundable_flights.py

from __future__ import annotations
from time import perf_counter
import time
import random
import json
from datetime import datetime
from typing import Any, Dict, Optional

import pytz
import requests
import frappe

BASE_URL = "https://myflyright.com/get-current-flight-incidents/"
# ---------- Notification mit Raven ----------

RAVEN_BOT_NAME = "flugninja"       # Name deines Raven Bots
RAVEN_CHANNEL_ID = "flight-alerts" # Channel-Name/-ID in Raven

# Helper: Channel-Referenz (Name oder #alias) -> echte Docname-ID auflösen
def _get_raven_channel_id(ref: str) -> str:
    """Gibt den Docname einer Raven Channel-Instanz zurück.
    ref darf 'name' (Docname) oder 'channel_name' oder '#channel_name' sein.
    """
    if not ref:
        raise frappe.ValidationError("Channel reference is empty")

    ref = ref.strip()
    if ref.startswith("#"):
        ref = ref[1:]

    # 1) Treffer als Docname?
    name = frappe.db.exists("Raven Channel", ref)
    if name:
        return name

    # 2) Treffer über das Feld 'channel_name'?
    name = frappe.db.get_value("Raven Channel", {"channel_name": ref}, "name")
    if name:
        return name

    raise frappe.LinkValidationError(f"Raven Channel not found: {ref}")

def _send_raven_summary(stats: Dict[str, Any]) -> None:
    """Postet eine Markdown-Zusammenfassung in Raven."""
    try:
        bot = frappe.get_doc("Raven Bot", RAVEN_BOT_NAME)
        channel_id = _get_raven_channel_id(RAVEN_CHANNEL_ID)
        # hübsches Markdown
        md = (
            f"## ✈️ Flight Import Summary\n"
            f"**Run:** {stats['started_at']} → {stats['ended_at']}  \n"
            f"**Duration:** {stats['duration_seconds']:.1f}s  \n"
            f"\n"
            f"- **API total_count:** {stats['api_total_count']}  \n"
            f"- **Pages processed:** {stats['pages']}  \n"
            f"- **Flights fetched:** {stats['fetched']}  \n"
            f"- **Created:** {stats['created']}  \n"
            f"- **Duplicates skipped:** {stats['duplicates']}  \n"
            f"- **Failed:** {stats['failed']}  \n"
        )

        bot.send_message(
            channel_id=channel_id,
            text=md,
            markdown=True
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "raven_summary_failed")

# ---------- Helpers (Frappe ORM) ----------

def _parse_local_dt(date_str: str, time_str: str, tz_name: str = "Europe/Vienna") -> tuple[str, str]:
    """wandelt DD/MM/YYYY + HH:MM in MySQL-Strings (datetime, date) in lokaler TZ."""
    tz = pytz.timezone(tz_name)
    dt_naive = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
    dt_local = tz.localize(dt_naive)
    return dt_local.strftime("%Y-%m-%d %H:%M:%S"), dt_local.strftime("%Y-%m-%d")


def check_flight_exists(data: Dict[str, Any]) -> bool:
    """Dublettencheck per Frappe-DB (flight_number + flight_date)."""
    try:
        raw_date = (data.get("formattedDate") or "").strip()
        flight_number = (data.get("flightNumber") or "").strip().upper()
        if not raw_date or not flight_number:
            return False

        flight_date = datetime.strptime(raw_date, "%d/%m/%Y").date()
        # Schnell & günstig:
        return bool(frappe.db.exists("Flight", {
            "flight_number": flight_number,
            "flight_date": flight_date
        }))
    except Exception:
        frappe.log_error(frappe.get_traceback(), "check_flight_exists failed")
        return False


def create_flight(data: Dict[str, Any]) -> Optional[str]:
    """Erstellt einen Flight über Frappe ORM. Gibt den Docname zurück oder None bei Fehler."""
    try:
        # Zeit parsen
        # data['time'] hat z. B. "13:25 CET" -> wir nehmen nur HH:MM vor dem Leerzeichen
        time_part = (data.get("time") or "").strip().split(" ")[0]
        dt_mysql, flight_date = _parse_local_dt(data["formattedDate"], time_part)

        doc = frappe.get_doc({
            "doctype": "Flight",
            "departure_city": data.get("from"),
            "arrival_city": data.get("to"),
            "possible_claim": float(data.get("claim") or 0) if data.get("claim") not in (None, "") else 0.0,
            "flight_number": (data.get("flightNumber") or "").strip().upper(),
            "flight_status": data.get("flightStatus"),
            "airline": data.get("airline"),
            "datetime": dt_mysql,
            "flight_date": flight_date
        })

        # Absicherung gegen Race Conditions: vor insert nochmal prüfen
        if frappe.db.exists("Flight", {
            "flight_number": doc.flight_number,
            "flight_date": doc.flight_date
        }):
            frappe.logger().info(f"[Flight] Duplicate avoided: {doc.flight_number} {doc.flight_date}")
            return None

        doc.insert(ignore_permissions=True)
        # Falls das in einem Background Job läuft, committen:
        frappe.db.commit()

        frappe.logger().info(f"[Flight] Created {doc.flight_number} @ {dt_mysql}")
        return doc.name

    except Exception:
        frappe.log_error(frappe.get_traceback(), "create_flight failed")
        return None


# ---------- Externer Fetch (bleibt via requests) ----------

def fetch_flights_raw(offset: int) -> Dict[str, Any]:
    """Holt Rohdaten einer Seite von der MyFlyRight-API."""
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    try:
        resp = requests.post(BASE_URL, headers=headers, data={"offset": offset}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        frappe.logger().warning(f"MyFlyRight load failed @ offset {offset}: {e}")
        return {}


# ---------- Orchestrierung ----------

def fetch_refundable_flights():
    """Batch-Import: zieht seitenweise, legt neue Flüge an (intern via Frappe ORM) und schickt am Ende eine Raven-Zusammenfassung."""
    start_perf = perf_counter()

    # Stats
    stats = {
        "api_total_count": None,
        "pages": 0,
        "fetched": 0,
        "created": 0,
        "duplicates": 0,
        "failed": 0,
        "started_at": frappe.utils.now(),  # server time
        "ended_at": None,
        "duration_seconds": 0.0,
    }

    offset = 0
    page_size = 50
    max_empty_pages = 3
    empty_pages = 0
    total_count = None

    try:
        while True:
            time.sleep(random.uniform(1.0, 2.5))
            response = fetch_flights_raw(offset=offset)

            if not response or "flights" not in response:
                empty_pages += 1
                frappe.logger().info(f"Empty page @ {offset} ({empty_pages}/{max_empty_pages})")
                if empty_pages >= max_empty_pages:
                    frappe.logger().info("Stop: too many empty pages.")
                    break
                offset += page_size
                continue

            flights = response.get("flights") or []
            stats["pages"] += 1
            stats["fetched"] += len(flights)

            if total_count is None:
                total_count = int(response.get("count") or 0)
                stats["api_total_count"] = total_count
                frappe.logger().info(f"Found total {total_count} flights.")

            frappe.logger().info(f"Process page offset {offset} ({len(flights)} flights)")
            for flight in flights:
                try:
                    if check_flight_exists(flight):
                        stats["duplicates"] += 1
                        continue

                    name = create_flight(flight)
                    if name:
                        stats["created"] += 1
                    else:
                        # create_flight hat geloggt; hier zählen wir als failed
                        stats["failed"] += 1
                except Exception:
                    stats["failed"] += 1
                    frappe.log_error(frappe.get_traceback(), "process_single_flight failed")

            offset += page_size
            if total_count and offset >= total_count:
                frappe.logger().info("All flights processed.")
                break
    finally:
        # Ende/Duration + Raven-Report
        stats["ended_at"] = frappe.utils.now()
        stats["duration_seconds"] = perf_counter() - start_perf
        _send_raven_summary(stats)



# ---------- Public API (optional aus Client/Scheduler aufrufen) ----------

@frappe.whitelist()
def run_fetch_refundable_flights_now() -> str:
    """Sofort ausführen (Sync). Für Debug/Manuell."""
    fetch_refundable_flights()
    return "done"


@frappe.whitelist()
def enqueue_fetch_refundable_flights() -> str:
    """In Queue legen (empfohlen)."""
    frappe.enqueue(
        "flugninja_case_management.flugninja_case_management.doctype.flight.fetch_refundable_flights.fetch_refundable_flights",
        queue="long", job_name="fetch_refundable_flights", timeout=60 * 20
    )
    return "enqueued"