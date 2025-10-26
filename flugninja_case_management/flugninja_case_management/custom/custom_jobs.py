import frappe
from frappe.utils import now_datetime, get_datetime
from datetime import datetime

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