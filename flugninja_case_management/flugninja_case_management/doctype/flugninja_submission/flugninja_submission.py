# Copyright (c) 2025, Pascal Schott and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from flugninja_case_management.flugninja_case_management.custom.flugninja_case_management import (
    create_or_get_assignment_contract,
    maybe_send_payout_completed_email,
    send_contract_email,
)


class FlugNinjaSubmission(Document):
    def validate(self):
        self._normalize_privacy_acknowledgement()
        self._ensure_submission_datetime()
        self._sync_payout_tracking_fields()
        representative = self._get_representative()
        self._sync_representative_fields(representative)
        self._validate_consents()

    def on_submit(self):
        contract, created = create_or_get_assignment_contract(self)
        if created:
            send_contract_email(self, contract)

    def on_update(self):
        maybe_send_payout_completed_email(self)

    def _get_representative(self):
        if not self.persons:
            frappe.throw("At least one passenger is required.")

        representative = self.persons[0]
        if not representative.firstname or not representative.lastname:
            frappe.throw("The representative must include first name and last name.")

        return representative

    def _sync_representative_fields(self, representative):
        self.representative_firstname = representative.firstname
        self.representative_email = representative.email or ""
        self.representative_contact = representative.phone or ""

    def _normalize_privacy_acknowledgement(self):
        if not self.privacy_acknowledged and self.privacy_accepted:
            self.privacy_acknowledged = 1

    def _ensure_submission_datetime(self):
        if not self.submission_datetime:
            self.submission_datetime = now_datetime()

    def _sync_payout_tracking_fields(self):
        if self.payout_status == "paid" and not getattr(self, "payout_completed_at", None):
            self.payout_completed_at = now_datetime()

    def _validate_consents(self):
        if not self.terms_accepted:
            frappe.throw("Terms and conditions must be accepted.")

        if not self.privacy_acknowledged:
            frappe.throw("Privacy notice must be acknowledged.")
