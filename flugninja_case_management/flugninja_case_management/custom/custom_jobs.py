import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime
from flugninja_case_management.flugninja_case_management.custom.flugninja_case_management import (
    maybe_send_payout_completed_email,
    maybe_send_review_request_email,
)


def check_and_expire_contract_urls():
    try:
        current_datetime = now_datetime()
        contracts = frappe.get_all(
            "Contract",
            filters={
                "custom_url_expired": 0,
                "custom_contract_type": "Assignment",
                "custom_expires_at": ["is", "set"],
            },
            fields=["name", "custom_expires_at"],
        )

        expired_count = 0
        for contract in contracts:
            expires_at = contract.get("custom_expires_at")
            if not expires_at:
                continue

            if current_datetime >= get_datetime(expires_at):
                frappe.db.set_value(
                    "Contract",
                    contract.name,
                    "custom_url_expired",
                    1,
                    update_modified=True,
                )
                expired_count += 1

        if expired_count:
            frappe.db.commit()
            frappe.logger().info(f"Expired {expired_count} contract URL(s) in this run")

        return {
            "status": "success",
            "expired_count": expired_count,
            "checked_at": current_datetime,
        }
    except Exception as error:
        frappe.log_error(message=str(error), title="Contract URL Expiry Job Failed")
        return {"status": "failed", "error": str(error)}


def send_contract_reminders():
    now = now_datetime()
    expiry_threshold = add_to_date(now, hours=24)
    filters = [
        ["custom_contract_type", "=", "Assignment"],
        ["custom_expires_at", ">=", now],
        ["custom_expires_at", "<=", expiry_threshold],
        ["status", "=", "Unsigned"],
        ["custom_reminder_sent", "=", 0],
        ["custom_url_expired", "=", 0],
    ]

    contracts = frappe.get_all(
        "Contract",
        filters=filters,
        fields=[
            "name",
            "custom_sign_url",
            "custom_expires_at",
            "custom_flugninja_reference",
            "custom_contract_type",
        ],
    )

    sent_count = 0
    for contract in contracts:
        try:
            send_reminder_email(contract)
            frappe.db.set_value("Contract", contract.name, "custom_reminder_sent", 1)
            sent_count += 1
        except Exception as error:
            frappe.log_error(f"Failed for {contract.name}: {str(error)}", "Reminder Error")

    if sent_count:
        frappe.db.commit()

    return {"status": "success", "sent_count": sent_count}


def send_reminder_email(contract):
    if not contract.custom_flugninja_reference:
        return

    try:
        submission = frappe.get_doc("FlugNinja Submission", contract.custom_flugninja_reference)
    except frappe.DoesNotExistError:
        return

    representative = submission.persons[0] if submission.persons else None
    if not representative or not representative.email:
        return

    try:
        email_template = frappe.get_doc("Email Template", "Reminder for Contract")
    except frappe.DoesNotExistError:
        frappe.log_error("Email Template 'Reminder for Contract' not found")
        return

    expires_at_str = frappe.utils.format_datetime(contract.custom_expires_at, "dd.MM.yyyy HH:mm")
    email_content = frappe.render_template(
        email_template.response_html,
        {
            "doc": submission,
            "contract": contract,
            "representative_full_name": " ".join(
                part for part in [representative.firstname or "", representative.lastname or ""] if part
            ).strip() or representative.email,
            "sign_url": contract.custom_sign_url or "",
            "contract_type": contract.custom_contract_type or "",
            "expires_at": expires_at_str,
        },
    )

    frappe.sendmail(
        recipients=representative.email,
        subject=email_template.subject,
        message=email_content,
        delayed=False,
    )


def send_payout_completion_notifications():
    sent_count = 0
    for submission_name in frappe.get_all(
        "FlugNinja Submission",
        filters={"payout_status": "paid"},
        pluck="name",
    ):
        try:
            submission = frappe.get_doc("FlugNinja Submission", submission_name)
            if maybe_send_payout_completed_email(submission):
                sent_count += 1
        except Exception as error:
            frappe.log_error(f"Failed for {submission_name}: {str(error)}", "Payout Email Error")

    if sent_count:
        frappe.db.commit()

    return {"status": "success", "sent_count": sent_count}


def send_review_request_emails():
    sent_count = 0
    for submission_name in frappe.get_all(
        "FlugNinja Submission",
        filters={"payout_status": "paid"},
        pluck="name",
    ):
        try:
            submission = frappe.get_doc("FlugNinja Submission", submission_name)
            if maybe_send_review_request_email(submission):
                sent_count += 1
        except Exception as error:
            frappe.log_error(f"Failed for {submission_name}: {str(error)}", "Review Email Error")

    if sent_count:
        frappe.db.commit()

    return {"status": "success", "sent_count": sent_count}
