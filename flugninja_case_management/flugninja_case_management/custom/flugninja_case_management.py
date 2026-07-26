import frappe
from frappe.utils import add_days, add_to_date, get_url, now_datetime


ASSIGNMENT_CONTRACT_TEMPLATE = "Assignment-DE-2025"
CONTRACT_EMAIL_TEMPLATE = "FlugNinja Contract Options"
CONTRACT_EMAIL_SUBJECT_TEMPLATE = "Bitte unterschreibe deinen Vertrag – {{ doc.flight_number }}"
CONTRACT_VALIDITY_DAYS = 7
DEFAULT_CUSTOMER_GROUP = "Individual"
DEFAULT_TERRITORY = "All Territories"
GOOGLE_REVIEW_URL_CONF_KEY = "flugninja_google_review_url"


def create_contracts(doc, method=None):
    contract, created = create_or_get_assignment_contract(doc)
    if created:
        send_contract_email(doc, contract)
    return contract


def create_or_get_assignment_contract(submission):
    representative = _get_representative_person(submission)
    existing = _get_existing_assignment_contracts(submission.name)

    if len(existing) > 1:
        frappe.throw(
            f"Multiple assignment contracts already exist for submission {submission.name}. Please clean up duplicates first."
        )

    if existing:
        contract = frappe.get_doc("Contract", existing[0].name)
        _sync_assignment_url(submission, contract.custom_sign_url)
        return contract, False

    contract = _build_assignment_contract(submission, representative)
    contract.insert()
    contract.custom_sign_url = _build_contract_sign_url(contract.custom_sign_token)
    contract.save()

    _sync_assignment_url(submission, contract.custom_sign_url)
    return contract, True


def send_contract_email(submission, assignment_contract):
    representative = _get_representative_person(submission)
    if not representative.email:
        frappe.throw("Representative email is required to send the contract.")

    email_template = frappe.get_doc("Email Template", CONTRACT_EMAIL_TEMPLATE)
    template_context = {
        "doc": submission,
        "representative_full_name": _get_representative_full_name(representative),
        "assignment_sign_url": assignment_contract.custom_sign_url,
        "expires_at": assignment_contract.custom_expires_at,
    }
    email_content = frappe.render_template(email_template.response_html, template_context)
    subject_template = email_template.subject or CONTRACT_EMAIL_SUBJECT_TEMPLATE
    email_subject = frappe.render_template(subject_template, template_context).strip()
    _send_submission_email(representative.email, email_subject, email_content)


def maybe_send_payout_completed_email(submission):
    if submission.payout_status != "paid" or getattr(submission, "payout_notification_sent_at", None):
        return False

    representative = _get_representative_person(submission)
    if not representative.email:
        return False

    payout_completed_at = getattr(submission, "payout_completed_at", None) or now_datetime()
    if not getattr(submission, "payout_completed_at", None):
        submission.db_set("payout_completed_at", payout_completed_at, update_modified=False)
        submission.payout_completed_at = payout_completed_at

    context = {
        "doc": submission,
        "representative_firstname": representative.firstname or _get_representative_full_name(representative),
        "payout_completed_at": payout_completed_at,
        "payout_amount": submission.possible_compensation or 0,
    }
    subject = frappe.render_template("Deine Auszahlung wurde veranlasst – {{ doc.flight_number }}", context).strip()
    message = frappe.render_template(_build_payout_email_html(), context)
    _send_submission_email(representative.email, subject, message)

    sent_at = now_datetime()
    submission.db_set("payout_notification_sent_at", sent_at, update_modified=False)
    submission.payout_notification_sent_at = sent_at
    return True


def maybe_send_review_request_email(submission):
    if submission.payout_status != "paid":
        return False
    if getattr(submission, "review_request_sent_at", None):
        return False

    payout_completed_at = getattr(submission, "payout_completed_at", None)
    if not payout_completed_at:
        return False

    if now_datetime() < add_to_date(payout_completed_at, days=7):
        return False

    representative = _get_representative_person(submission)
    if not representative.email:
        return False

    context = {
        "doc": submission,
        "representative_firstname": representative.firstname or _get_representative_full_name(representative),
        "google_review_url": frappe.conf.get(GOOGLE_REVIEW_URL_CONF_KEY, ""),
        "review_text": (
            "Ich bin mit FlugNinja sehr zufrieden. Die Bearbeitung war schnell, "
            "klar und unkompliziert. Vielen Dank für die professionelle Unterstützung."
        ),
    }
    subject = "Wie zufrieden warst du mit FlugNinja?"
    message = frappe.render_template(_build_review_request_email_html(), context)
    _send_submission_email(representative.email, subject, message)

    sent_at = now_datetime()
    submission.db_set("review_request_sent_at", sent_at, update_modified=False)
    submission.review_request_sent_at = sent_at
    return True


def sign_contract(contract_name, signee, ip_address):
    contract = frappe.get_doc("Contract", contract_name)
    if contract.is_signed:
        frappe.throw("Contract already signed.")
    if contract.custom_expires_at and contract.custom_expires_at < now_datetime():
        frappe.throw("Contract link has expired.")

    contract.update(
        {
            "is_signed": 1,
            "signee": signee,
            "signed_on": now_datetime(),
            "ip_address": ip_address,
            "status": "Active",
        }
    )
    contract.save()


@frappe.whitelist()
def resend_contract_links(submission_name):
    submission = frappe.get_doc("FlugNinja Submission", submission_name)
    contracts = _get_existing_assignment_contracts(submission_name)

    if not contracts:
        frappe.throw("No assignment contract found for this submission.")
    if len(contracts) != 1:
        frappe.throw(f"Expected 1 assignment contract, found {len(contracts)}. Please check the contracts.")

    contract_info = contracts[0]
    if contract_info.is_signed == 1:
        frappe.throw("Cannot resend links. The assignment contract has already been signed.")
    if contract_info.status in ["Inactive", "Disabled"]:
        frappe.throw(f"Cannot resend links. The assignment contract is {contract_info.status}.")

    contract = frappe.get_doc("Contract", contract_info.name)
    current_time = now_datetime()
    contract.custom_sign_token = frappe.generate_hash(length=32)
    contract.custom_sign_url = _build_contract_sign_url(contract.custom_sign_token)
    contract.custom_sent_at = current_time
    contract.custom_expires_at = add_days(current_time, CONTRACT_VALIDITY_DAYS)
    contract.custom_reminder_sent = 0
    contract.custom_url_expired = 0
    contract.status = "Unsigned"
    contract.save()

    _sync_assignment_url(submission, contract.custom_sign_url)
    send_contract_email(submission, contract)

    representative = _get_representative_person(submission)
    recipient = representative.email or "the representative"
    frappe.msgprint(f"Contract link has been resent to {recipient}")

    return {"success": True, "message": "Contract link resent successfully"}


def _build_assignment_contract(submission, representative):
    representative_firstname = representative.firstname or _get_representative_full_name(representative)
    customer_name = _get_or_create_customer(representative)

    return frappe.get_doc(
        {
            "doctype": "Contract",
            "party_type": "Customer",
            "party_name": customer_name,
            "custom_contract_type": "Assignment",
            "submission": submission.name,
            "contract_template": ASSIGNMENT_CONTRACT_TEMPLATE,
            "contract_terms": frappe.render_template(
                frappe.get_value("Contract Template", ASSIGNMENT_CONTRACT_TEMPLATE, "contract_terms"),
                {"doc": submission, "representative_firstname": representative_firstname},
            ),
            "status": "Unsigned",
            "custom_sent_at": now_datetime(),
            "custom_expires_at": add_days(now_datetime(), CONTRACT_VALIDITY_DAYS),
            "custom_sign_token": frappe.generate_hash(length=32),
            "custom_flugninja_reference": submission.name,
        }
    )


def _get_or_create_customer(representative):
    representative_email = representative.email or ""
    if representative_email:
        existing_customer = frappe.db.get_value("Customer", {"email_id": representative_email}, "name")
        if existing_customer:
            return existing_customer

    customer_name = _get_representative_full_name(representative)
    customer = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Individual",
            "customer_group": DEFAULT_CUSTOMER_GROUP,
            "territory": DEFAULT_TERRITORY,
            "email_id": representative_email,
            "mobile_no": representative.phone or "",
        }
    )
    customer.insert(ignore_permissions=True)
    return customer.name


def _get_existing_assignment_contracts(submission_name):
    return frappe.get_all(
        "Contract",
        filters={
            "custom_flugninja_reference": submission_name,
            "custom_contract_type": "Assignment",
        },
        fields=["name", "status", "is_signed", "custom_expires_at", "custom_sign_url"],
        order_by="creation asc",
    )


def _get_representative_person(submission):
    representative = submission.persons[0] if submission.persons else None
    if not representative:
        frappe.throw("No representative found in Passenger table.")
    return representative


def _get_representative_full_name(representative):
    parts = [representative.firstname or "", representative.lastname or ""]
    full_name = " ".join(part for part in parts if part).strip()
    return full_name or representative.email or representative.name


def _sync_assignment_url(submission, sign_url):
    if getattr(submission, "assignment_url", None) == sign_url:
        return

    submission.assignment_url = sign_url
    if getattr(submission, "name", None):
        submission.db_set("assignment_url", sign_url, update_modified=False)


def _build_contract_sign_url(token):
    return f"{get_url()}/contract-details?token={token}"


def _send_submission_email(recipient, subject, message):
    if not getattr(frappe.local, "assets_json", None):
        frappe.local.assets_json = {}

    frappe.sendmail(
        recipients=recipient,
        subject=subject,
        message=message,
        delayed=False,
    )


def _build_payout_email_html():
    return """
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f7f9;margin:0;padding:24px;">
  <tr>
    <td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(15,23,42,0.06);">
        <tr>
          <td style="background:#2563EB;padding:18px 20px;">
            <table role="presentation" width="100%">
              <tr>
                <td align="left" style="font:700 28px/1.1 Poppins,-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#ffffff;letter-spacing:-0.04em;">FlugNinja</td>
                <td align="right" style="font:600 12px/1.2 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#eaf2ff;">Auszahlungsbestätigung</td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:28px 28px 8px 28px;">
            <h1 style="margin:0;font:700 22px/1.3 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">Deine Auszahlung wurde veranlasst</h1>
            <p style="margin:10px 0 0 0;font:400 16px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#334155;">Hallo {{ representative_firstname }},</p>
            <p style="margin:8px 0 0 0;font:400 16px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#334155;">wir haben die Auszahlung für deinen Fall veranlasst.</p>
          </td>
        </tr>
        <tr>
          <td style="padding:4px 28px 0 28px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;">
              <tr>
                <td style="padding:16px 18px;">
                  <h2 style="margin:0 0 8px 0;font:700 16px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">Auszahlungsdetails</h2>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font:400 14px/1.7 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#334155;">
                    <tr><td style="padding:2px 0;width:42%;">Fallreferenz</td><td style="padding:2px 0;"><strong>{{ doc.name }}</strong></td></tr>
                    <tr><td style="padding:2px 0;">Flugnummer</td><td style="padding:2px 0;"><strong>{{ doc.flight_number or "-" }}</strong></td></tr>
                    <tr><td style="padding:2px 0;">Auszahlungsbetrag</td><td style="padding:2px 0;"><strong>{{ payout_amount }} EUR</strong></td></tr>
                    <tr><td style="padding:2px 0;">Veranlasst am</td><td style="padding:2px 0;"><strong>{{ frappe.utils.format_datetime(payout_completed_at) }}</strong></td></tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:16px 28px 0 28px;">
            <p style="margin:0;font:400 14px/1.7 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#475569;">Falls du Fragen zur Auszahlung hast, antworte einfach auf diese E-Mail.</p>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 28px 26px 28px;">
            <p style="margin:0 0 6px 0;font:400 13px/1.7 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#475569;">Viele Grüße<br><strong style="color:#0f172a;">Dein FlugNinja-Team</strong></p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


def _build_review_request_email_html():
    return """
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f7f9;margin:0;padding:24px;">
  <tr>
    <td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(15,23,42,0.06);">
        <tr>
          <td style="background:#2563EB;padding:18px 20px;">
            <table role="presentation" width="100%">
              <tr>
                <td align="left" style="font:700 28px/1.1 Poppins,-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#ffffff;letter-spacing:-0.04em;">FlugNinja</td>
                <td align="right" style="font:600 12px/1.2 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#eaf2ff;">Bewertungsanfrage</td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:28px 28px 8px 28px;">
            <h1 style="margin:0;font:700 22px/1.3 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">Hat alles gut funktioniert?</h1>
            <p style="margin:10px 0 0 0;font:400 16px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#334155;">Hallo {{ representative_firstname }},</p>
            <p style="margin:8px 0 0 0;font:400 16px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#334155;">wenn du mit FlugNinja zufrieden warst, würden wir uns sehr über eine 5-Sterne-Bewertung auf Google freuen.</p>
          </td>
        </tr>
        <tr>
          <td style="padding:4px 28px 0 28px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;">
              <tr>
                <td style="padding:16px 18px;">
                  <h2 style="margin:0 0 8px 0;font:700 16px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">Vorschlag für deine Bewertung</h2>
                  <div style="padding:14px 16px;border-radius:10px;background:#ffffff;border:1px solid #e2e8f0;font:400 14px/1.7 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#334155;">
                    {{ review_text }}
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        {% if google_review_url %}
        <tr>
          <td style="padding:16px 28px 0 28px;">
            <a href="{{ google_review_url }}" style="display:inline-block;background:#2563EB;color:#ffffff;text-decoration:none;font:700 15px/48px -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:0 22px;border-radius:12px;">Jetzt auf Google bewerten</a>
          </td>
        </tr>
        {% endif %}
        <tr>
          <td style="padding:16px 28px 0 28px;">
            <p style="margin:0;font:400 14px/1.7 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#475569;">Vielen Dank für dein Vertrauen und deine Unterstützung.</p>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 28px 26px 28px;">
            <p style="margin:0 0 6px 0;font:400 13px/1.7 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#475569;">Viele Grüße<br><strong style="color:#0f172a;">Dein FlugNinja-Team</strong></p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""
