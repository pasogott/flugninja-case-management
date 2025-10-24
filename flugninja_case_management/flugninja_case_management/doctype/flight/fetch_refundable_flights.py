# -*- coding: utf-8 -*-
# /home/frappeuser/frappe-bench/apps/flugninja_case_management/...
# flugninja_case_management/doctype/flight/fetch_refundable_flights.py

from __future__ import annotations
import time
import random
import json
from datetime import datetime
from typing import Any, Dict, Optional

import pytz
import requests
import frappe

BASE_URL = "https://myflyright.com/get-current-flight-incidents/"

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
    """Batch-Import: zieht Seitenweise, legt neue Flüge an (intern via Frappe ORM)."""
    offset = 0
    page_size = 50
    max_empty_pages = 3
    empty_pages = 0
    total_count = None

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
        if total_count is None:
            total_count = int(response.get("count") or 0)
            frappe.logger().info(f"Found total {total_count} flights.")

        frappe.logger().info(f"Process page offset {offset} ({len(flights)} flights)")
        for flight in flights:
            try:
                if not check_flight_exists(flight):
                    create_flight(flight)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "process_single_flight failed")

        offset += page_size
        if total_count and offset >= total_count:
            frappe.logger().info("All flights processed.")
            break


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