import base64
import re

import frappe
from frappe.utils import now_datetime
from frappe.utils.file_manager import save_file


DEFAULT_TERMS_TEXT = "Keine Vertragsbedingungen hinterlegt."
PAYOUT_STATUS_AWAITING_SIGNATURE = "awaiting_signature"
PAYOUT_STATUS_AWAITING_BANK_DETAILS = "awaiting_bank_details"
PAYOUT_STATUS_READY_FOR_PAYOUT = "ready_for_payout"
PAYOUT_STATUS_PAID = "paid"


def clean_text_editor_content(html_content):
    if not html_content:
        return DEFAULT_TERMS_TEXT

    try:
        cleaned = html_content
        cleaned = re.sub(r'<div class="ql-editor[^"]*">', '', cleaned)
        cleaned = cleaned.replace('</div>', '')
        cleaned = re.sub(r'<li[^>]*>', '- ', cleaned)
        cleaned = cleaned.replace('</li>', '\n')
        cleaned = re.sub(r'<p[^>]*>', '', cleaned)
        cleaned = cleaned.replace('</p>', '\n')
        cleaned = re.sub(r'<br\s*/?>', '\n', cleaned)
        cleaned = re.sub(r'<span[^>]*>', '', cleaned)
        cleaned = cleaned.replace('</span>', '')
        cleaned = re.sub(r'<[^>]+>', '', cleaned)
        cleaned = cleaned.replace('&nbsp;', ' ')
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        cleaned = re.sub(r'[ \t]+', ' ', cleaned)
        cleaned = cleaned.strip()
        return cleaned or DEFAULT_TERMS_TEXT
    except Exception as exc:
        frappe.log_error(f"Error cleaning contract terms: {exc}")
        return DEFAULT_TERMS_TEXT


def normalize_iban(value):
    return re.sub(r'\s+', '', (value or '')).upper().strip()


def is_valid_iban(value):
    iban = normalize_iban(value)
    if not re.fullmatch(r'[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}', iban):
        return False

    rearranged = iban[4:] + iban[:4]
    numeric = ''.join(str(ord(char) - 55) if char.isalpha() else char for char in rearranged)
    return int(numeric) % 97 == 1


def get_submission(contract):
    reference = contract.get('custom_flugninja_reference')
    if not reference:
        return None

    return frappe.db.get_value(
        'FlugNinja Submission',
        reference,
        [
            'name',
            'possible_compensation',
            'iban',
            'account_holder',
            'bank_details_completed_at',
            'payout_status',
        ],
        as_dict=True,
    )


def get_value(source, fieldname):
    if isinstance(source, dict):
        return source.get(fieldname)
    return getattr(source, fieldname, None)


def has_bank_details(source):
    return bool(get_value(source, 'iban') and get_value(source, 'account_holder'))


def is_contract_completed(contract, submission):
    if not contract or not submission:
        return False
    if not get_value(contract, 'is_signed'):
        return False
    if not has_bank_details(submission):
        return False
    return get_value(submission, 'payout_status') in {
        PAYOUT_STATUS_READY_FOR_PAYOUT,
        PAYOUT_STATUS_PAID,
    }


def determine_payout_status(contract_is_signed, submission):
    if not has_bank_details(submission):
        return PAYOUT_STATUS_AWAITING_BANK_DETAILS
    if not contract_is_signed:
        return PAYOUT_STATUS_AWAITING_SIGNATURE
    if get_value(submission, 'payout_status') == PAYOUT_STATUS_PAID:
        return PAYOUT_STATUS_PAID
    return PAYOUT_STATUS_READY_FOR_PAYOUT


def sync_submission_payout_status(submission, contract_is_signed):
    submission.payout_status = determine_payout_status(contract_is_signed, submission)


def enrich_contract(contract, submission=None):
    contract['contract_terms'] = clean_text_editor_content(contract.get('contract_terms'))
    submission = submission or get_submission(contract)
    contract['submission'] = submission or {}
    contract['payout_amount'] = contract.get('custom_possible_compensation') or (submission or {}).get('possible_compensation')
    contract['iban'] = (submission or {}).get('iban')
    contract['account_holder'] = (submission or {}).get('account_holder')
    contract['bank_details_completed_at'] = (submission or {}).get('bank_details_completed_at')
    contract['payout_status'] = determine_payout_status(bool(contract.get('is_signed')), submission)
    return contract


def get_contract_or_error(token):
    if not token:
        return None, 'Der Vertragslink ist unvollständig.'

    contracts = frappe.get_all(
        'Contract',
        filters={
            'custom_sign_token': token,
        },
        fields=[
            'name',
            'party_type',
            'party_name',
            'is_signed',
            'custom_sent_at',
            'custom_expires_at',
            'status',
            'custom_contract_type',
            'custom_flugninja_reference',
            'start_date',
            'end_date',
            'signee',
            'signed_on',
            'contract_template',
            'contract_terms',
            'signee_company',
            'custom_sign_token',
            'custom_signature',
            'custom_contract_status',
            'custom_possible_compensation',
            'custom_url_expired',
        ],
        limit_page_length=1,
    )

    if not contracts:
        return None, 'Dieser Vertragslink ist ungültig oder abgelaufen.'

    contract = contracts[0]
    if contract.get('custom_contract_status') == 'Disabled':
        return None, 'Dieser Vertragslink ist nicht mehr gültig.'

    submission = get_submission(contract)
    if is_contract_completed(contract, submission):
        return None, 'Dieser Vertragslink wurde bereits abgeschlossen.'

    if contract.get('custom_url_expired'):
        return None, 'Dieser Vertragslink ist abgelaufen.'

    expires_at = contract.get('custom_expires_at')
    if expires_at and expires_at < now_datetime():
        return None, 'Dieser Vertragslink ist abgelaufen.'

    return enrich_contract(contract, submission), None


@frappe.whitelist(allow_guest=True)
def get_contracts(contract_name=None, token=None):
    contract, error = get_contract_or_error(token)
    if error:
        return {'error': error}
    return [contract]


@frappe.whitelist(allow_guest=True)
def submit_signature(contract_name=None, token=None, signature=None):
    try:
        contract_data, error = get_contract_or_error(token)
        if error:
            return {'error': error}

        contract = frappe.get_doc('Contract', contract_data['name'])
        if contract.is_signed:
            return {'error': 'Dieser Vertrag wurde bereits unterschrieben.'}

        if not has_bank_details(contract_data):
            return {'error': 'Bitte hinterlege zuerst IBAN und Kontoinhaber.'}

        if not signature:
            return {'error': 'Bitte füge zuerst deine Unterschrift hinzu.'}

        base64_string = signature.split(',', 1)[1] if signature.startswith('data:image') else signature
        image_data = base64.b64decode(base64_string)

        filename = f"signature_{contract.name}_{frappe.utils.now_datetime().strftime('%Y%m%d_%H%M%S')}.png"
        file_doc = save_file(
            filename,
            image_data,
            'Contract',
            contract.name,
            is_private=1,
        )

        contract.custom_signature = file_doc.file_url
        contract.is_signed = 1
        contract.status = 'Active'
        contract.signed_on = now_datetime()
        contract.signee = contract.signee or contract.party_name
        contract.save(ignore_permissions=True)

        if contract.custom_flugninja_reference:
            submission = frappe.get_doc('FlugNinja Submission', contract.custom_flugninja_reference)
            sync_submission_payout_status(submission, True)
            submission.save(ignore_permissions=True)

        contract.custom_url_expired = 1
        contract.save(ignore_permissions=True)

        frappe.db.commit()
        return {'message': 'success', 'deactivated': True}
    except Exception as exc:
        frappe.log_error(f"Signature Submit Error: {exc}")
        return {'error': 'Die Unterschrift konnte nicht gespeichert werden. Bitte versuche es erneut.'}


@frappe.whitelist(allow_guest=True)
def submit_bank_details(token=None, iban=None, account_holder=None):
    try:
        contract_data, error = get_contract_or_error(token)
        if error:
            return {'error': error}

        submission_name = contract_data.get('custom_flugninja_reference')
        if not submission_name:
            return {'error': 'Zum Vertrag wurde kein Fall gefunden.'}

        normalized_iban = normalize_iban(iban)
        cleaned_account_holder = (account_holder or '').strip()

        if not cleaned_account_holder:
            return {'error': 'Bitte gib den Kontoinhaber ein.'}
        if not is_valid_iban(normalized_iban):
            return {'error': 'Bitte gib eine gültige IBAN ein.'}

        submission = frappe.get_doc('FlugNinja Submission', submission_name)
        submission.account_holder = cleaned_account_holder
        submission.iban = normalized_iban
        submission.bank_details_completed_at = now_datetime()
        sync_submission_payout_status(submission, bool(contract_data.get('is_signed')))
        submission.save(ignore_permissions=True)

        frappe.db.commit()
        return {
            'message': 'success',
            'deactivated': False,
            'payout_status': submission.payout_status,
            'payout_amount': submission.possible_compensation,
            'iban': submission.iban,
            'account_holder': submission.account_holder,
            'bank_details_completed_at': submission.bank_details_completed_at,
        }
    except Exception as exc:
        frappe.log_error(f"Bank details submit error: {exc}")
        return {'error': 'Die Kontodaten konnten nicht gespeichert werden. Bitte versuche es erneut.'}
