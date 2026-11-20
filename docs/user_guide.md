# Heritage 360 First Time User Guide

Version 1.0 · September 2026

Heritage 360 gives Heritage and its clients one project workspace for approvals, finish selections, documents, messages, estimates, change orders, invoices, and financial status. This guide covers the first setup steps for company users and the normal portal experience for clients.

The recommended starting sequence is simple. A company administrator completes company settings and team roles. A project manager creates a project, assigns staff, and invites the client. Staff then prepare and publish the information the client should see. Both sides use the Action center to find work that needs attention.

## Contents

1. Signing in
2. Company setup
3. Creating the first project
4. Roles and project access
5. Project dashboard and Action center
6. Messages
7. Documents and photos
8. Estimates
9. Finish selections
10. Change orders
11. Invoices and payments
12. Project financials
13. Schedule
14. QuickBooks
15. Client portal walkthrough
16. Common questions
17. Pilot feature notes

## Signing In

Open Heritage 360 and select **Log in**. Use the email address connected to your account. Select **Forgot your password** if you need a reset link.

New team members and clients normally join through an email invitation. The invitation must be opened by the person who owns the invited email address. Invitations expire after seven days. A company user with permission can resend an expired invitation.

After signing in, the Projects page lists only the projects you can access. A company administrator sees every project in the company. Other internal users see assigned projects. Clients see only projects to which they have been invited.

## Company Setup

Company administrators should complete these steps before a client begins using the portal.

### Confirm the company workspace

Open **Company** from the main navigation and select the company. This screen contains team roles, invitations, company settings, links to cost codes and tax settings.

### Set the default tax rate

Open **Tax settings** and enter the company default. New estimate and invoice drafts use this rate. Staff can change the rate on an individual draft before it is finalized.

### Create cost codes

Open **Cost codes** and add the codes used to organize project pricing. Cost codes can represent products, materials, labor, subcontractors, commission or markup, allowances, tax, and other project costs.

Use clear codes and names that match the company's accounting process. A cost code used on a QuickBooks invoice must be mapped to a QuickBooks Product or Service item before synchronization.

### Invite the internal team

Select **Invite team member**, enter the person's email, and assign a role. The available roles are Admin, Manager, Project manager, Staff, Office manager, Real estate agent, and Accountant.

A company role does not automatically give every non-admin user access to every project. Assign project access after creating the project.

## Creating the First Project

From **Projects**, select **New project** and enter the following information:

- Company
- Project name
- Internal project code
- Description
- Project status
- Start date
- Target completion date

The person who creates the project receives management and client-invitation access to that project. Company administrators can access all company projects.

After creating the project, open **People and access**.

### Assign internal users

Select an internal team member and choose the project permissions they need:

- **Manage project workflows** allows the user to create and update project records such as documents, estimates, selections, change orders, and invoices.
- **Invite and manage clients** allows the user to send client invitations and manage client access.
- **Receive project email notifications** includes the user in applicable project emails.

Only give each person the access needed for their job. Access can be updated or revoked later.

### Invite the client

Select **Invite customer** and enter the exact email address the client will use. The client receives a secure link to create an account or connect the project to an existing account.

The People and access screen shows active clients and invitation history. Use this screen to resend an invitation, revoke an unused invitation, or revoke and restore client project access.

### Add the starting project information

Before asking the client to review the portal, add the information that should be available on the first visit:

- Current plans, contracts, and supporting documents
- The estimate or proposal
- Open finish selections and their options
- Any active change orders
- A welcome message or project question
- Issued invoices, if applicable

Review the client-visible setting on each document. Draft estimates, draft change orders, draft selections, internal documents, internal costs, margins, and the project schedule do not belong in the client portal.

## Roles and Project Access

| Role | Typical access |
| --- | --- |
| Admin | All company projects, company and team settings, QuickBooks, cost codes, tax settings |
| Manager | Assigned projects and management workflows; can create projects |
| Project manager | Assigned projects and day-to-day project workflows; can create projects |
| Staff | Assigned projects; available actions depend on project permissions |
| Office manager | Assigned projects; available actions depend on project permissions |
| Real estate agent | Assigned projects; available actions depend on project permissions |
| Accountant | Assigned financial records and accounting work without client messages, documents, the Action center, or schedule access |
| Client | Assigned client projects and client-visible information only |

The company role sets the general access category. The project assignment controls which projects a non-admin internal user can open. The permission switches under People and access control the management buttons shown for that project.

## Project Dashboard and Action Center

The Projects page is the main dashboard. It shows assigned projects, project status, and the number of items needing attention. Use the search and status filters when the project list grows.

The Action center collects the work that needs attention in the selected project:

- Client decisions waiting on documents, selections, and change orders
- Internal drafts that have not been sent or published
- Delayed internal schedule milestones
- Open project conversations

Company users see internal workflow items for projects they can access. Clients see only their own decisions and client-visible conversations. Clients do not see the internal schedule.

## Messages

Use Messages for project-specific communication that should remain with the job record.

1. Open the project and select **Messages**.
2. Start a conversation with a clear subject.
3. Add the first message and submit it.
4. Continue the discussion in the same thread.
5. Close the conversation when the question is resolved. Reopen it if more discussion is needed.

Messages trigger email notifications to the applicable project participants. Keep one topic in each thread so the history remains easy to find.

## Documents and Photos

### Upload a company document

Open **Documents** and select **Upload document**. Enter a title and description, choose a category, and upload the file. Then decide whether the file is visible to clients and whether it requires client approval.

If more than one client approval is required, set the required approval count before sending the document. The document becomes fully approved only after the required number of distinct clients approve the current version.

### Replace a document with a new version

Open the document and upload a new version instead of creating a separate document. Heritage 360 retains the version history and records client decisions against the version that was reviewed.

### Request an electronic signature

Open a client-visible PDF document and select **Request signature**. Choose one active project
client, add optional instructions and a due date, and send the request. The request is tied to the
exact PDF version using a SHA-256 fingerprint. If the stored bytes change, the application blocks
the signature instead of accepting it against a different file.

The client signs in through the portal, downloads and reviews the PDF, consents to electronic
signing, and types their full legal name. They may also decline and provide a reason. The completed
record includes the signer, consent language, date and time, document fingerprint, technical request
information, and a downloadable PDF audit certificate. Upload a new document version before sending
a replacement request if the content changes.

### Client uploads

Clients can open Documents and select **Upload files or photos**. The upload appears in the project and assigned internal users receive a notification. Progress photos show the upload date with the file record.

Supported document and image formats include common PDF, Office, text, spreadsheet, JPEG, PNG, HEIC, and WebP files. The default maximum upload size is 25 MB per file.

## Estimates

An estimate establishes the proposed full project price.

1. Open **Estimates** and select **New estimate**.
2. Enter the title, description, client notes, tax rate, and required number of client approvals.
3. Add line items with category, cost code, quantity, client unit price, and internal unit cost.
4. Review the price, cost, tax, and margin totals.
5. Submit the estimate to the client.
6. The client approves or declines it in the portal.

The first approved estimate sets the project's fixed contract amount. Only one estimate can become the approved contract estimate. If a client declines the estimate, staff can revise it or create a replacement while preserving the history.

## Finish Selections

Use Selections for finishes, materials, allowances, and client choices.

### Create and publish a selection

1. Open **Selections** and select **New selection**.
2. Enter the title, location, allowance amount, due date, and client instructions.
3. Add each available option. Options can include vendor information, product links, specifications, images, attachments, lead time, price, and internal cost.
4. Group related selections into a package when the client needs to make several choices for one area.
5. Publish the selection when it is ready for the client.

### After the client chooses

Heritage 360 compares the selected price with the allowance.

- An overage is flagged for staff and includes a shortcut to prepare a change order.
- An unused credit is flagged for staff. The company can apply it elsewhere, return it at closing, or retain it as margin according to the project decision.
- A client request for an option outside the published list is routed to staff and can be used to prepare a change order.

Clients cannot change a completed selection themselves. An authorized company user must reopen it.

## Change Orders

Use Change orders when project scope, price, cost, or schedule changes.

1. Open **Change orders** and select **New change order**.
2. Describe the change and its reason.
3. Enter the client price change, internal cost change, schedule change, and required number of approvals.
4. Add itemized cost-code lines when detailed pricing is needed.
5. Submit the change order to the client.
6. The client approves or declines it in the portal and may add a comment.

An approved positive change order can be converted into an invoice draft. An approved client credit can flow through the credit memo process. If a declined change order needs more work, staff can revise it or create a linked replacement. If an approved change order is later voided, staff must review the financial and QuickBooks effects.

## Invoices and Payments

Invoices can begin as manual drafts or can be created from approved project work.

1. Open **Invoices** and create a draft, or use the accounting handoff on an approved change order or selection.
2. Add or review the client, due date, notes, tax rate, and line items.
3. Issue the invoice when the totals are correct. Issuing assigns the permanent invoice number and makes the invoice visible to the client.
4. Download the invoice PDF when a printable copy is needed.
5. Record payments manually while the Desktop integration is being prepared.

Issued invoice totals and line items are intentionally locked. Correct an issued invoice through the supported void and reissue process rather than changing its history.

Clients can view invoice details, download the PDF, see the amount paid and balance due, and ask a question through Messages. Online payment is not available in the current release.

## Project Financials

Open **Project financials** for the consolidated project totals.

Clients can see the contract amount, approved and pending change orders, selection allowances and variances, invoices, payments, and remaining balance.

Authorized management and accounting roles can also see internal budget, committed costs, actual costs, estimated final cost, margin, profitability, and selection-credit disposition. These internal values are not shown to clients.

Staff can record actual project costs in the job-costing ledger. Use a clear description, date, category, cost code, amount, and note so each entry can be audited later.

## Schedule

The Schedule module is for internal project planning. Admins, Managers, and Project managers can add and update milestones. Other assigned internal users may have read-only access. Clients and Accountants do not have schedule access.

Each milestone can include a start date, end date, status, description, internal notes, and display order. Mark delayed work accurately so it appears in the internal Action center.

## QuickBooks

Heritage uses QuickBooks Desktop. Administrators can open **QuickBooks** in the
navigation or **QuickBooks setup** from Company to save onboarding details.
Save partial information now and complete it as details are confirmed.

Collect the edition and year, Windows computer or hosting provider, number of
company files, setup contact, customer/job structure, invoice items, tax handling,
invoice numbering, and credit workflow. Do not enter passwords or company-file paths.

The setup screen does not connect to QuickBooks or transfer accounting records.
The Desktop adapter still requires development and validation with a test company
file before an accounting administrator approves the first live transfer. Intuit
Web Connector is the planned Windows connection method. QuickBooks Online
connection controls remain unavailable in Heritage.

## Client Portal Walkthrough

A client can use this short sequence on the first visit.

1. Open the invitation email and follow the secure link.
2. Create a password or sign in with the invited email address.
3. Open the project from **Projects**.
4. Select **Action center** to see approvals and choices waiting for a decision.
5. Open a document, estimate, or change order to review and approve or decline it.
6. Open Selections to choose from published finish options.
7. Use Messages for project questions.
8. Use Documents to view shared files or upload a photo or document to the project team.
9. Use Invoices and Project financials to review issued charges, payments, and the remaining balance.

After submitting a decision or finish choice, contact the project team through Messages if something must change. The project team can revise the item or reopen the selection while keeping the original activity history.

## Common Questions

### Why can the client not see an item

Confirm that the item is no longer a draft and has been submitted or published. For documents, also confirm **Client visible** is enabled. Finally, confirm the client has active access to the correct project.

### Why can a team member not see a project

Except for company administrators, internal users need an active project assignment. Open **People and access**, assign the team member, and confirm the permission switches.

### Why did the invitation fail

The invitation may be expired, revoked, already accepted, or opened while signed in with a different email address. Resend it and have the recipient use the exact invited email.

### Why will an invoice not synchronize

Heritage’s Desktop connection is not active yet. Open QuickBooks setup to review the details still needed. Do not use the separate QuickBooks Online connection flow for a Desktop company file.

### Why can the invoice no longer be edited

Issued invoices are locked to protect the billing record. Use the supported void and reissue process for a correction.

### Where should users begin each day

Start on Projects. Open the project with the highest attention count, then use Action center to review pending decisions, drafts, delays, and open conversations.

## Pilot Feature Notes

The current pilot includes the core client portal, project access, messaging, documents and uploads, approvals, estimates, finish selections, change orders, invoices, financial summaries, internal schedule milestones, audit history, and administrator-only QuickBooks Desktop setup preparation.

The current release does not include online invoice payment, tasks and punch lists, two-factor authentication, schedule dependencies, recurring schedule items, or external calendar synchronization. Production use also requires verified persistent file storage, database backups, email delivery, monitoring, legal details, and verified accounting setup.


## Punch lists and selection deadlines

Open a project and choose **Punch list**. Staff with project management access
can add an item, choose its location and due date, and assign an active project
team member. Items start internal. Check **Share this item and its photos with
clients** only when the description, photos and captions are ready for the client.
Internal notes and assignment details stay private. Clients can view shared
items but cannot change them.

The assignee can start work and mark it **Ready for verification**. A staff member
with management access then selects **Verify complete** after checking the work.
Use **Reopen** for further work or **Cancel item** to retain a record without
keeping it on the active list. Filter by status or **Assigned to me**. Open the
item to add up to 20 photos, one at a time; staff managers can remove individual
photos. Photos use the item's sharing setting.

On a pending selection, staff can use **Selection deadline** to change its due
date or pause scheduled reminders. Punch-list items have the same reminder
checkbox in their edit form. When the daily reminder service is enabled,
reminders are sent 3 days before, 1 day before, on the due date, 1 day overdue,
and weekly after that. Selection reminders go to primary clients; punch-list
reminders go to the assignee. Ready-for-verification, verified and cancelled
punch items do not generate deadline reminders. Projects on hold or completed
also stop scheduled reminders.

Use each project's **Notification settings** to choose email and/or in-app
alerts. The selection **Send reminder** button remains available to staff;
repeated clicks do not send multiple reminders to the same person that day.

## Internal staff messages

Open **Staff messages** in the navigation bar, then **New conversation**. Choose
the company and either a project or **General conversation — no project**.
Select the staff recipients, enter a subject and message, and send. The project's
**Staff messages** card opens conversations for that project.

Only the selected participants can read and reply. Clients and subcontractors
cannot use this inbox. Project conversations also require current internal access
to that project. Administrators are not automatically included in conversations.
The existing client/project Messages area remains separate.

Recipients receive an in-app alert and an email containing the message and a link
to open the conversation. Sign in to reply in Heritage; replying to the email
does not post a message. Email delivery normally takes up to five minutes. Use
the inbox search, **General**, project context, and **Unread only** filters to
find conversations. Opening the newest messages marks their alerts as read.

Uncheck **Email me about new replies** and save to mute email for one
conversation. In-app alerts remain enabled. Removing someone's company or
project access also prevents further access and stops queued email to them.


## Allowances and project totals

Open **Allowances & project totals** from the project dashboard or Selections. Primary clients see only their own project’s published customer-facing information. Staff with project financial access can also use the sheet. It is available in staff’s client preview.

The sheet itemizes approved change orders and shows each priced selection category’s selected price, allowance, overage/credit, amount already in linked approved change orders, and remaining adjustment. Categories with no priced amount are omitted; an unselected allowance is never treated as savings. Draft and pending change orders stay outside the total, and clients never see drafts. Internal costs, margins, vendor details, and staff notes are not included.

**Original contract price + net adjustment = new project total.** Net adjustment combines approved change orders with selection overages/credits not already in linked approved change orders. Existing manually entered overages may not be linked: review overlap before final billing. This is a working price reconciliation, not the invoice balance. It does not deduct payments or applied credit memos, issue invoices, approve change orders, or transfer records to QuickBooks. Credits remain subject to the contract’s terms and project review.

Use **Print / save PDF** or **Download spreadsheet** (CSV) to retain a copy. The same access rules apply to the download. The Selections list and card views also include category/location subtotals and a grand total for the visible filtered selections, with unpriced allowances separate. Remaining allowance is green under, red over, and neutral at zero.
