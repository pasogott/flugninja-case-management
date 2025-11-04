import frappe
from frappe.utils import now_datetime, get_datetime
from datetime import datetime
from datetime import datetime
from frappe.utils import now_datetime, add_to_date
def check_and_expire_contract_urls():
    """
    Hourly job to check and expire contract URLs
    Checks if current datetime >= custom_expires_at
    If yes, sets custom_url_expired = 1
    """
    try:
        current_datetime = now_datetime()
        
        # Fetch all contracts where:
        # 1. custom_url_expired = 0 (not expired yet)
        # 2. custom_expires_at is set
        contracts = frappe.get_all(
            "Contract",
            filters={
                "custom_url_expired": 0,
                "custom_expires_at": ["is", "set"]
            },
            fields=["name", "custom_expires_at", "custom_sign_token"]
        )
        
        expired_count = 0
        
        for contract in contracts:
            if contract.get("custom_expires_at"):
                expires_at = get_datetime(contract.custom_expires_at)
                
                # Check if current datetime >= expires_at
                if current_datetime >= expires_at:
                    # Update the contract to mark URL as expired
                    frappe.db.set_value(
                        "Contract",
                        contract.name,
                        "custom_url_expired",
                        1,
                        update_modified=True
                    )
                    expired_count += 1
                    
                    frappe.logger().info(
                        f"Contract {contract.name} URL expired at {expires_at}"
                    )
        
        # Commit the changes
        frappe.db.commit()
        
        if expired_count > 0:
            frappe.logger().info(
                f"Expired {expired_count} contract URL(s) in this run"
            )
        
        return {
            "status": "success",
            "expired_count": expired_count,
            "checked_at": current_datetime
        }
        
    except Exception as e:
        frappe.log_error(
            message=str(e),
            title="Contract URL Expiry Job Failed"
        )
        return {
            "status": "failed",
            "error": str(e)
        }
      
 
        
# @frappe.whitelist(allow_guest=True)
def send_contract_reminders():
    now = now_datetime()
    expiry_threshold = add_to_date(now, hours=24)

    # CORRECT FILTERS
    filters = {
        "custom_expires_at": [">=", now],                    # Must be in future or now
        "custom_expires_at": ["<=", expiry_threshold],       # Within next 24 hours
        "status": "Unsigned",
        "custom_reminder_sent": 0                            # Int 0, not 0.0
    }

    contracts = frappe.get_all(
        "Contract",
        filters=filters,
        fields=[
            "name", "custom_sign_url", "custom_expires_at",
            "custom_flugninja_reference", "custom_contract_type"
        ],
        debug=True
    )

    frappe.log_error(f"Found {len(contracts)} contracts for reminder", "Debug Reminder")

    for contract in contracts:
        try:
            send_reminder_email(contract)
            frappe.db.set_value("Contract", contract.name, "custom_reminder_sent", 1)
            frappe.db.commit()
        except Exception as e:
            frappe.log_error(f"Failed for {contract.name}: {str(e)}", "Reminder Error")


def send_reminder_email(contract):
    """
    Send reminder email using FlugNinja submission and representative
    """
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

    # Format expiry time
    expires_at_str = frappe.utils.format_datetime(contract.custom_expires_at, "dd.MM.yyyy HH:mm")

    # Render template
    email_content = frappe.render_template(
        email_template.response_html,
        {
            "doc": submission,
            "contract": contract,
            "representative_full_name": representative.name,
            "sign_url": contract.custom_sign_url or "",
            "contract_type": contract.custom_contract_type or "",
            "expires_at": expires_at_str
        }
    )

    # Send email
    frappe.sendmail(
        recipients=representative.email,
        subject=email_template.subject,
        message=email_content,
        delayed=False
    )



