// Copyright (c) 2025, Pascal Schott and contributors
// For license information, please see license.txt

// frappe.ui.form.on("FlugNinja Submission", {
// 	refresh(frm) {

// 	},
// });



frappe.ui.form.on('FlugNinja Submission', {
    refresh: function(frm) {
        // Show button only if there are unsigned contracts
        if (!frm.doc.__islocal) {
            frm.add_custom_button(__('Resend Contract Links'), function() {
                frappe.call({
                    method: 'flugninja_case_management.flugninja_case_management.custom.flugninja_case_management.resend_contract_links',
                    args: {
                        submission_name: frm.doc.name
                    },
                    callback: function(r) {
                        if (r.message && r.message.success) {
                            frappe.show_alert({
                                message: __('Contract links resent successfully'),
                                indicator: 'green'
                            });
                        }
                    }
                });
            });
        }
    }
});