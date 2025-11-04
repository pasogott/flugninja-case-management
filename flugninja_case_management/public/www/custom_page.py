import frappe
from frappe.utils import get_url
import re
import base64
import os
from frappe.utils import get_files_path

def clean_text_editor_content(html_content):
    """
    Clean HTML content from Frappe Text Editor - Simple version
    """
    if not html_content:
        return "No terms specified"
    
    try:
        # Remove ql-editor wrapper
        html_content = re.sub(r'<div class="ql-editor[^"]*">', '', html_content)
        html_content = html_content.replace('</div>', '')
        
        # Replace <p> tags with newlines
        html_content = re.sub(r'<p[^>]*>', '', html_content)
        html_content = html_content.replace('</p>', '\n')
        
        # Replace <br> with newlines
        html_content = html_content.replace('<br>', '\n')
        html_content = html_content.replace('<br/>', '\n')
        html_content = html_content.replace('<br />', '\n')
        
        # Remove all remaining HTML tags
        html_content = re.sub(r'<span[^>]*>', '', html_content)
        html_content = html_content.replace('</span>', '')
        html_content = re.sub(r'<[^>]+>', '', html_content)
        
        # Clean up whitespace
        html_content = re.sub(r'\n{3,}', '\n\n', html_content)
        html_content = html_content.strip()
        
        return html_content if html_content else "No terms specified"
        
    except Exception as e:
        frappe.log_error(f"Error cleaning HTML content: {str(e)}")
        return "Error processing terms"

# @frappe.whitelist(allow_guest=True)
# def get_contracts(contract_name=None, token=None):
#     """
#     Public API to fetch a specific contract using name + token validation
#     """
#     try:
#         if not contract_name or not token:
#             return {"error": "Contract name and token are required"}

#         contracts = frappe.get_all(
#             "Contract",
#             filters={
#                 "name": contract_name,
#                 "custom_sign_token": token,
#                 "custom_url_expired": 0
#             },
#             fields=[
#                 "name",
#                 "party_type",
#                 "party_name",
#                 "is_signed",
#                 "custom_sent_at",
#                 "custom_expires_at",
#                 "status",
#                 "custom_contract_type",
#                 "custom_flugninja_reference",
#                 "start_date",
#                 "end_date",
#                 "signee",
#                 "signed_on",
#                 "contract_template",
#                 "contract_terms",
#                 "signee_company",
#                 "custom_sign_token",
#                 "custom_signature"
#             ],
#             limit_page_length=1
#         )

#         if not contracts:
#             return {"error": "Invalid contract or token"}

#         # Clean the contract_terms HTML content
#         for contract in contracts:
#             if contract.get('contract_terms'):
#                 contract['contract_terms'] = clean_text_editor_content(contract['contract_terms'])
#             if contract.get('custom_signature') and contract['custom_signature'].startswith('/files'):
#                 contract['custom_signature'] = get_url() + contract['custom_signature']
#         return contracts

#     except Exception as e:
#         frappe.log_error(f"Public Contract Fetch Error: {str(e)}")
#         return {"error": str(e)}

@frappe.whitelist(allow_guest=True)
def get_contracts(contract_name=None, token=None):
    """
    Public API to fetch a specific contract using name + token validation
    """
    try:
        # if not contract_name or not token:
        #     return {"error": "Contract name and token are required"}

        contracts = frappe.get_all(
            "Contract",
            filters={
                # "name": contract_name,
                "custom_sign_token": token,
                "custom_url_expired": 0
            },
            fields=[
                "name",
                "party_type",
                "party_name",
                "is_signed",
                "custom_sent_at",
                "custom_expires_at",
                "status",
                "custom_contract_type",
                "custom_flugninja_reference",
                "start_date",
                "end_date",
                "signee",
                "signed_on",
                "contract_template",
                "contract_terms",
                "signee_company",
                "custom_sign_token",
                "custom_signature",
                "custom_contract_status"
            ],
            limit_page_length=1
        )

        if not contracts:
            return {"error": "Invalid contract or token"}

        # Check if the contract status is Disabled
        for contract in contracts:
            if contract.get('custom_contract_status') == "Disabled":
                return {"error": "This contract has been Disabled"}

        # Clean the contract_terms HTML content
        for contract in contracts:
            if contract.get('contract_terms'):
                contract['contract_terms'] = clean_text_editor_content(contract['contract_terms'])
            if contract.get('custom_signature') and contract['custom_signature'].startswith('/files'):
                contract['custom_signature'] = get_url() + contract['custom_signature']

        return contracts

    except Exception as e:
        frappe.log_error(f"Public Contract Fetch Error: {str(e)}")
        return {"error": str(e)}
    
@frappe.whitelist(allow_guest=True)
def submit_signature(contract_name = None, token = None, signature= None):
    """
    Save digital signature to Contract Doctype in custom_signature field (Attach Image)
    """
    try:
        # if not contract_name or not token or not signature:
        #     return {"error": "Missing required parameters"}

        # Validate contract and token
        contract = frappe.get_value(
            "Contract",
            # {"name": contract_name, "custom_sign_token": token},
            {"custom_sign_token": token},
            ["name"]
        )
        if not contract:
            return {"error": "Invalid contract or token"}

        # Extract base64 data
        if signature.startswith("data:image"):
            base64_string = signature.split(",")[1]
        else:
            base64_string = signature

        # Decode base64 to binary
        image_data = base64.b64decode(base64_string)

        # Generate a unique filename
        # filename = f"signature_{contract_name}_{frappe.utils.now_datetime().strftime('%Y%m%d_%H%M%S')}.png"
        filename = f"signature_{contract}_{frappe.utils.now_datetime().strftime('%Y%m%d_%H%M%S')}.png"
        file_path = os.path.join(get_files_path(), filename)

        # Save the image
        with open(file_path, "wb") as f:
            f.write(image_data)

        # Create File record
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": filename,
            "file_url": f"/files/{filename}",
            "is_private": 1,
            "attached_to_doctype": "Contract",
            # "attached_to_name": contract_name,
            "attached_to_name": contract,
            "content": image_data,
        })
        file_doc.save(ignore_permissions=True)

        # Update Contract
        doc = frappe.get_doc("Contract", contract)
        doc.custom_signature = file_doc.file_url
        doc.is_signed = 1
        doc.status = "Signed"
        doc.signed_on = frappe.utils.now()
        doc.save(ignore_permissions=True)
        
        custom_flugninja_reference = doc.custom_flugninja_reference
        if custom_flugninja_reference:
            # Find the other contract linked to the same submission
            other_contracts = frappe.get_all(
                "Contract",
                filters={
                    "custom_flugninja_reference": custom_flugninja_reference,
                    # "name": ["!=", contract_name],  # Exclude the current signed contract
                    "name": ["!=", contract],
                    "custom_sign_token": ["!=", token]
                },
                fields=["name"]
            )
            for other_contract in other_contracts:
                other_doc = frappe.get_doc("Contract", other_contract.name)
                other_doc.custom_contract_status = "Disabled"  # Set to Disabled
                other_doc.save(ignore_permissions=True)    
                    
        frappe.db.commit()

        return {"message": "success"}

    except Exception as e:
        frappe.log_error(f"Signature Submit Error: {str(e)}")
        # More specific error handling
        if "Permission denied" in str(e):
            return {"error": "Server permission error. Contact admin."}
        return {"error": str(e) or "Unable to save signature."}
    
    