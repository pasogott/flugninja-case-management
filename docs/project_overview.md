— Automated Contract Workflow (No-Code Design)

## Purpose
The **FlugNinja Contract System** automates the creation, sending, and signing of legal contracts for flight compensation claims.
Its goal is to ensure a smooth, legally safe, and *no-code editable* workflow from claim submission to signed contracts.

---

## Key Design Goals
- **Fully automated trigger** when a Flugninja Submission is submitted.
- **Two contracts** are always created together (Assignment & Success Fee).
- **Template-based, no-code** text editing for both contracts and emails.
- **Automatic email sending** with two signing links.
- **Expiration logic** for contract links (e.g. 7 days).
- **Tracking & reminders** for sent links.
- **UI button** to resend contracts manually if needed.
- **Editability:** Contracts and email templates must remain editable in Frappe (no code deployment).

---

## Workflow Overview

### Step 1 — Flugninja Submission Received
A traveler submits a claim on the website → a **Flugninja Submission** document is created in Frappe.
After manual review, staff **submit** the document.

### Step 2 — Automated Contract Bundle Creation (On Submit)
When the Submission is submitted:
1. The system automatically creates a **Contract Bundle** linked to the submission.
2. The bundle includes:
   - Representative info (name, email, phone)
   - Passenger list
   - Two contracts:
     - **Assignment Contract** (Immediate Payout)
     - **Success Fee Contract** (Higher payout if successful)
3. Both contracts are generated from editable templates (see below).
4. Two **unique, secure signing links** are created.
5. An **email (based on a template)** is sent automatically to the representative:
   - Includes both links as buttons:
     “Sign for Immediate Payout” and “Sign for Success Fee Option”.

---

## Step 3 — Digital Signing
When the user opens a contract link:
- The system checks if the contract has already been signed.
  - If yes → show message: “This contract has already been signed.”
- If not yet signed:
  - Show the rendered contract text (from template) in a web form.
  - Allow digital signature.
  - On sign:
    - Save signature image, IP address, and timestamp.
    - Update status to **Signed**.
    - Mark the *other* contract in the bundle as **Disabled**.
    - Update bundle status to **Completed**.

---

## Step 4 — Link Expiration & Reminders
- Each signing link includes an **expiration timestamp** (e.g. 7 days after sending).
- Expired links display a message:
  “This link has expired. Please contact support@flugninja.at.”
- The bundle tracks:
  - `sent_at` – when the email with links was sent.
  - `expires_at` – calculated automatically (e.g. `sent_at + 7 days`).
- The system can send **reminder notifications** before expiration.
- A **“Resend Contract Links”** button in the Frappe UI allows staff to manually resend them.

---

## Editable Elements (No-Code)

### ✍️ Contract Templates
- Managed through a `Contract Template` DocType.
- Editable via **Rich Text** fields.
- Variables use Jinja syntax (e.g. `{{ representative_firstname }}`).
- Each template has:
  - Template name (e.g. “Assignment-DE-2025”)
  - Type (Assignment / Success Fee)
  - Rich text content
  - Language / version metadata
  - Active flag (boolean)

### 💌 Email Templates
- Managed as `Email Template` or `Notification` in Frappe.
- Editable via Frappe’s built-in editor.
- Placeholders support:
  - `{{ doc.representative_firstname }}`
  - `{{ doc.assignment_sign_url }}`
  - `{{ doc.success_fee_sign_url }}`
- Can be styled with inline HTML (include logo, colors, etc.).

---

## Tracking Fields (Core)
| Field Name | Description | Type |
|-------------|--------------|------|
| `sent_at` | When the email with links was sent | Datetime |
| `expires_at` | When the links expire | Datetime |
| `reminder_sent_at` | Optional timestamp for reminder email | Datetime |
| `signed_at` | When the contract was signed | Datetime |
| `status` | Draft / Sent / Signed / Expired / Disabled | Select |
| `sign_token` | Unique secure identifier per contract | Data |
| `sign_url` | Public URL including token | Data |

---

## Example Email Template (Editable in Frappe)
```html
<h3>✈️ Your FlugNinja Contract Options</h3>
<p>Hello {{ doc.representative_full_name }},</p>
<p>We have reviewed your flight claim. Please choose one of the following options:</p>
<ul>
  <li><a href="{{ doc.assignment_sign_url }}">💶 Immediate Payout (Assignment Contract)</a></li>
  <li><a href="{{ doc.success_fee_sign_url }}">⚖️ Higher Payout (Success Fee Contract)</a></li>
</ul>
<p>These links are valid until <strong>{{ frappe.utils.format_datetime(doc.expires_at) }}</strong>.</p>
<p>Best regards,<br><strong>Your FlugNinja Team</strong></p>
<p style="font-size:12px;color:#777">support@flugninja.at</p>
```

## Technology Stack
- **Framework:** [Frappe Framework](https://frappeframework.com/) (Python + MariaDB + Redis + React frontend)
- **Environment:** ERPNext-compatible backend, using DocTypes, Workflows, and Web Forms
- **Frontend:** Standard Frappe Web Form + Jinja templates
- **Storage:** Files and PDFs stored as `File` documents in Frappe
- **Automation:** Server-side hooks (Python) for `on_submit`, Email Templates, and Notifications
- **No-Code Editing:** Contracts and Emails are editable inside the Frappe Desk UI via Rich Text fields