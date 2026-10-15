# Construction Manager First Time User Guide

Version 1.0 · September 2026

Construction Manager gives a construction company and its clients one project workspace for approvals, finish selections, documents, messages, estimates, change orders, invoices, and financial status. This guide covers the first setup steps for company users and the normal portal experience for clients.

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

Open Construction Manager and select **Log in**. Use the email address connected to your account. Select **Forgot your password** if you need a reset link.

New team members and clients normally join through an email invitation. The invitation must be opened by the person who owns the invited email address. Invitations expire after seven days. A company user with permission can resend an expired invitation.

After signing in, the Projects page lists only the projects you can access. A company administrator sees every project in the company. Other internal users see assigned projects. Clients see only projects to which they have been invited.

## Company Setup

Company administrators should complete these steps before a client begins using the portal.

### Confirm the company workspace

Open **Company** from the main navigation and select the company. This screen contains team roles, invitations, company settings, subscription access, and links to cost codes and tax settings.

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
| Admin | All company projects, company and team settings, QuickBooks, cost codes, tax settings, and subscription controls |
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

Open the document and upload a new version instead of creating a separate document. Construction Manager retains the version history and records client decisions against the version that was reviewed.

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

Construction Manager compares the selected price with the allowance.

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
5. Record a manual payment or import payments from QuickBooks.

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

QuickBooks is the accounting source of truth. Company administrators control the connection and synchronization actions.

### Connect the company

1. Open **QuickBooks** from the main navigation.
2. Select the connection action and sign in to Intuit.
3. Choose the correct QuickBooks company.
4. Confirm the company name, environment, connection status, and available capabilities shown after connection.

### Map the project and cost codes

Each Construction Manager project maps to a QuickBooks Customer. The integration does not depend on QuickBooks Jobs. Match an existing customer when appropriate or create one through the supported sync action.

Each cost code used on an invoice line must map to a QuickBooks Product or Service item. Matching an existing QuickBooks item is the most reliable setup path.

### Synchronize invoices and payments

Issue the invoice in Construction Manager first. On the invoice detail page, use **Sync to QuickBooks**. The app blocks the sync if a required project customer or cost-code item mapping is missing.

Use **Sync payments from QuickBooks** on the invoice detail page to import and recheck payments and supported QuickBooks credit memos linked to that invoice. Review possible duplicates instead of recording the same payment twice.

Use the QuickBooks screen to review failed attempts. Correct the mapping or accounting issue before retrying or marking an exception resolved.

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

Confirm the QuickBooks company is connected, the project has a customer mapping, every invoice cost code has an active item mapping, and the QuickBooks company allows the requested action. Review the synchronization error before retrying.

### Why can the invoice no longer be edited

Issued invoices are locked to protect the billing record. Use the supported void and reissue process for a correction.

### Where should users begin each day

Start on Projects. Open the project with the highest attention count, then use Action center to review pending decisions, drafts, delays, and open conversations.

## Pilot Feature Notes

The current pilot includes the core client portal, project access, messaging, documents and uploads, approvals, estimates, finish selections, change orders, invoices, financial summaries, internal schedule milestones, audit history, and administrator-controlled QuickBooks integration.

The current release does not include online invoice payment, tasks and punch lists, two-factor authentication, schedule dependencies, recurring schedule items, or external calendar synchronization. Production use also requires verified persistent file storage, database backups, email delivery, monitoring, legal details, and live QuickBooks and subscription configuration.
