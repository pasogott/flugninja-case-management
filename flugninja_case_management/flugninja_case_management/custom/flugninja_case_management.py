import frappe
from frappe.utils import add_days, now_datetime, get_url

def create_contracts(doc, method):
    # Get representative details from Passenger child table
    representative = None
    for person in doc.persons:
        representative = person
        break  # Assuming first person is the representative
    
    if not representative:
        frappe.throw("No representative found in Passenger table.")
    
    base_url = get_url()
    
    # ----------------------------
    # ASSIGNMENT CONTRACT
    # ----------------------------
    assignment_contract = frappe.new_doc("Contract")
    assignment_contract.update({
        "party_type": "Customer",
        "party_name": "test",
        "custom_contract_type": "Assignment",
        "submission": doc.name,
        "contract_template": "Assignment-DE-2025",
        "contract_terms": frappe.render_template(
            frappe.get_value("Contract Template", "Assignment-DE-2025", "contract_terms"),
            {"doc": doc, "representative_firstname": representative.name}
        ),
        "status": "Unsigned",
        "custom_sent_at": now_datetime(),
        "custom_expires_at": add_days(now_datetime(), 7),
        "custom_sign_token": frappe.generate_hash(length=32),
        "custom_flugninja_reference": doc.name
    })
    assignment_contract.insert()
    
    # Generate Assignment URL
    assignment_url = (
        # f"{base_url}/contract-details?name={assignment_contract.name}&token={assignment_contract.custom_sign_token}"
        f"{base_url}/contract-details?token={assignment_contract.custom_sign_token}"
    )
    assignment_contract.custom_sign_url = assignment_url
    assignment_contract.save()
    
    # ----------------------------
    # SUCCESS FEE CONTRACT
    # ----------------------------
    success_fee_contract = frappe.new_doc("Contract")
    success_fee_contract.update({
        "party_type": "Customer",
        "party_name": "test",
        "custom_contract_type": "Success Fee",
        "submission": doc.name,
        "contract_template": "Success-Fee-DE-2025",
        "contract_terms": frappe.render_template(
            frappe.get_value("Contract Template", "Success-Fee-DE-2025", "contract_terms"),
            {"doc": doc, "representative_firstname": representative.name}
        ),
        "status": "Unsigned",
        "custom_sent_at": now_datetime(),
        "custom_expires_at": add_days(now_datetime(), 7),
        "custom_sign_token": frappe.generate_hash(length=32),
        "custom_flugninja_reference": doc.name
    })
    success_fee_contract.insert()
    
    # Generate Success Fee URL
    success_fee_url = (
        # f"{base_url}/contract-details?name={success_fee_contract.name}&token={success_fee_contract.custom_sign_token}"
        f"{base_url}/contract-details?token={success_fee_contract.custom_sign_token}"
    )
    success_fee_contract.custom_sign_url = success_fee_url
    success_fee_contract.save()
    
    # Store URLs back in FlugNinja Submission
    doc.assignment_url = assignment_url
    doc.fee_url = success_fee_url
    
    
    # Send email with signing links
    send_contract_email(doc, assignment_contract, success_fee_contract)
    
def send_contract_email(submission, assignment_contract, success_fee_contract):
    # Get representative details from first person
    representative = submission.persons[0] if submission.persons else None
    
    if not representative:
        frappe.throw("No representative found to send email.")
    
    email_template = frappe.get_doc("Email Template", "FlugNinja Contract Options")
    email_content = frappe.render_template(
        email_template.response_html,
        {
            "doc": submission,
            "representative_full_name": representative.name,
            "assignment_sign_url": assignment_contract.custom_sign_url,
            "success_fee_sign_url": success_fee_contract.custom_sign_url,
            "expires_at": assignment_contract.custom_expires_at
        }
    )
    frappe.sendmail(
        recipients=representative.email,
        subject=email_template.subject,
        message=email_content,
        delayed=False
    )


def sign_contract(contract_name, signee, ip_address):
    contract = frappe.get_doc("Contract", contract_name)
    if contract.is_signed:
        frappe.throw("Contract already signed.")
    if contract.expires_at < now_datetime():
        frappe.throw("Contract link has expired.")

    contract.update({
        "is_signed": 1,
        "signee": signee,
        "signed_on": now_datetime(),
        "ip_address": ip_address,
        "status": "Active"
    })
    contract.save()

    # Disable the other contract
    other_contracts = frappe.get_all(
        "Contract",
        filters={
            "submission": contract.submission,
            "contract_type": ["!=", contract.contract_type],
            "status": "Unsigned"
        },
        fields=["name"]
    )
    for oc in other_contracts:
        frappe.db.set_value("Contract", oc.name, "status", "Inactive")

@frappe.whitelist()
def resend_contract_links(submission_name):
    """
    Resend contract links for a FlugNinja Submission
    This function will:
    1. Validate both contracts are unsigned and not disabled
    2. Check if contracts are not expired
    3. Regenerate tokens and URLs
    4. Extend expiry dates
    5. Resend email to representative
    """
    # Get submission document
    submission = frappe.get_doc("FlugNinja Submission", submission_name)
    
    # Get ALL contracts for this submission (not just unsigned)
    all_contracts = frappe.get_all(
        "Contract",
        filters={
            "custom_flugninja_reference": submission_name
        },
        fields=["name", "custom_contract_type", "status", "is_signed", "custom_expires_at"]
    )
    
    if not all_contracts:
        frappe.throw("No contracts found for this submission.")
    
    if len(all_contracts) != 2:
        frappe.throw(f"Expected 2 contracts, found {len(all_contracts)}. Please check the contracts.")
    
    # Validate both contracts
    assignment_contract_info = None
    success_fee_contract_info = None
    
    for contract_info in all_contracts:
        # Check if contract is signed
        if contract_info.is_signed == 1:
            frappe.throw(
                f"Cannot resend links. The {contract_info.custom_contract_type} contract has already been signed."
            )
        
        # Check if contract is disabled/inactive
        if contract_info.status in ["Inactive", "Disabled"]:
            frappe.throw(
                f"Cannot resend links. The {contract_info.custom_contract_type} contract is {contract_info.status}."
            )
        
        # Check if contract is not unsigned
        if contract_info.status != "Unsigned":
            frappe.throw(
                f"Cannot resend links. The {contract_info.custom_contract_type} contract status is '{contract_info.status}'."
            )
        
        # Identify contract type
        if contract_info.custom_contract_type == "Assignment":
            assignment_contract_info = contract_info
        elif contract_info.custom_contract_type == "Success Fee":
            success_fee_contract_info = contract_info
    
    # Check if both contract types exist
    if not assignment_contract_info or not success_fee_contract_info:
        frappe.throw("Could not find both Assignment and Success Fee contracts.")
    
    # Check expiry for both contracts
    current_time = now_datetime()
    
    if assignment_contract_info.custom_expires_at and assignment_contract_info.custom_expires_at <= current_time:
        frappe.throw(
            f"Cannot resend links. The Assignment contract links have expired on {frappe.utils.format_datetime(assignment_contract_info.custom_expires_at)}."
        )
    
    if success_fee_contract_info.custom_expires_at and success_fee_contract_info.custom_expires_at <= current_time:
        frappe.throw(
            f"Cannot resend links. The Success Fee contract links have expired on {frappe.utils.format_datetime(success_fee_contract_info.custom_expires_at)}."
        )
    
    # All validations passed, now update contracts
    assignment_contract = frappe.get_doc("Contract", assignment_contract_info.name)
    success_fee_contract = frappe.get_doc("Contract", success_fee_contract_info.name)
    
    base_url = get_url()
    
    # Update Assignment Contract
    assignment_contract.custom_sign_token = frappe.generate_hash(length=32)
    assignment_contract.custom_sign_url = (
        # f"{base_url}/contract-details?name={assignment_contract.name}&token={assignment_contract.custom_sign_token}"
        f"{base_url}/contract-details?token={assignment_contract.custom_sign_token}"
    )
    assignment_contract.custom_sent_at = now_datetime()
    assignment_contract.custom_expires_at = add_days(now_datetime(), 7)
    assignment_contract.save()
    
    # Update Success Fee Contract
    success_fee_contract.custom_sign_token = frappe.generate_hash(length=32)
    success_fee_contract.custom_sign_url = (
        # f"{base_url}/contract-details?name={success_fee_contract.name}&token={success_fee_contract.custom_sign_token}"
        f"{base_url}/contract-details?token={success_fee_contract.custom_sign_token}"
    )
    success_fee_contract.custom_sent_at = now_datetime()
    success_fee_contract.custom_expires_at = add_days(now_datetime(), 7)
    success_fee_contract.save()
    
    # Resend email
    send_contract_email(submission, assignment_contract, success_fee_contract)
    
    frappe.msgprint(f"Contract links have been resent to {submission.persons[0].email}")
    
    return {
        "success": True,
        "message": "Contract links resent successfully"
    }


