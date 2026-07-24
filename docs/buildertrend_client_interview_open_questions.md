# Heritage Realty & Custom Homes

## Buildertrend Replacement - Outstanding Client Interview Questions

**Pilot project:** Duvally Residence  
**Purpose:** Resolve the remaining workflow and integration decisions before building the next major modules.

This checklist intentionally leaves out decisions that have already been confirmed. Start with Sections 1-4; those answers affect the next development work most directly.

## 1. QuickBooks Online Integration

1. Which records must the app exchange with QuickBooks Online?

   - Customers/clients
   - Projects/jobs
   - Vendors/subcontractors
   - Estimates
   - Change orders
   - Bills and expenses
   - Invoices
   - Payments
   - Time entries
   - Cost codes, budget amounts, and actual costs

2. For each record type, should the app only read from QuickBooks, only send to QuickBooks, or do both?

3. QuickBooks is the accounting source of truth. Which records, if any, should begin in this app before being approved and sent to QuickBooks?

4. At what point should an approved change order be sent or marked ready for QuickBooks?

5. Should synchronization happen immediately, on a schedule, or only when an authorized user selects **Sync**?

6. Does anything require approval before it is sent to QuickBooks? If so, what requires approval and who can approve it?

7. Who should receive and resolve synchronization errors or conflicting data: the admin, accountant, or both?

8. How are projects and costs organized in QuickBooks today? Ask about projects/jobs, products and services, classes, locations, and cost codes.

9. Is there one QuickBooks company file, and can a QuickBooks sandbox or test company be provided for development?

10. Does historical QuickBooks or Buildertrend data need to be imported, or will the app begin with active projects and new transactions only?

## 2. Invoices, Payments, and Financial Visibility

1. Are invoices part of the first release, or should they remain in QuickBooks only for now?

2. If invoices appear in the client portal, what should clients be able to do?

   - View invoice details
   - Download an invoice
   - See balances and payment status
   - Pay online
   - Ask a question about an invoice

3. How are invoices generated today: fixed payment schedule, milestone billing, progress/percentage billing, time and materials, or another method?

4. If online payments are required, which payment processor is used today? Should payment continue through QuickBooks Payments?

5. Exactly which project financial information should a client see when making decisions?

   - Contract amount
   - Approved and pending change orders
   - Selection allowances, overages, and credits
   - Invoices, payments, and remaining balance
   - Budget or cost information

6. Exactly which project-level financial information should staff see: budget, committed costs, actual costs, remaining budget, estimated final cost, or project profitability?

7. Should accountants have access to every project automatically, or only projects assigned to them?

## 3. Subcontractor Access

1. Do subcontractors need their own login in the first release?

2. If they do, what should they be able to view or do?

   - View assigned projects and selected schedule items
   - Receive and complete tasks or punch-list items
   - View or upload plans, documents, and photos
   - Participate in project messages
   - Submit bids, bills, invoices, or change requests
   - Complete daily logs

3. What financial information, if any, may a subcontractor see?

4. Should subcontractors see only information assigned directly to them, or broader project information?

5. Who may invite, remove, and manage subcontractors: admin only, or admin and staff?

6. Do vendors, designers, or other outside collaborators need a different type of limited access?

## 4. Change Order Workflow

1. Walk through a real change order from request to payment. Who requests it, prepares pricing, reviews it, sends it, approves it, and bills it?

2. Can clients request a change, or can only company staff initiate one?

3. What pricing details are required: line items, quantities, labor, materials, subcontractor costs, markup, tax, allowances, and cost codes?

4. If more than one client is assigned to a project, is one approval sufficient, or must every designated client approve?

5. What should happen after a client declines a change order: revise the existing version, create a new version, or close it?

6. Can an approved change order ever be cancelled or revised? If so, who may do that and what audit record is required?

7. How should an approved change order affect the contract total, project budget, schedule, invoice plan, and QuickBooks records?

8. Is authenticated approval still sufficient for change orders, or does this workflow require a formal e-signature later?

## 5. Client Portal, Documents, and Photos

1. Should clients be able to upload files and photos back to the company? If yes, where should those uploads appear and who should be notified?

2. Which document types require client approval or decline?

3. If a project has multiple clients, does one document decision count for the project, or is a decision required from each client?

4. Should a client be allowed to change an approval or selection after submitting it? If so, until what point?

5. Is a decline reason required for documents and change orders?

6. What file types and maximum file sizes must be supported?

7. Do progress photos need a gallery, dates, categories, captions, or client visibility controls?

8. Is email sufficient for notifications in the first release, or is text messaging also required?

9. Which events should send notifications, and should users receive each event immediately or in a digest?

## 6. Schedule, Tasks, and Field Operations

1. Does each project use one master schedule, or separate schedules for office staff, field staff, and subcontractors?

2. Should clients see the full project schedule or only selected milestones?

3. Who may create and update schedule items, and who should be notified when dates change?

4. Are dependencies, recurring items, calendar views, and external calendar integration required?

5. Are tasks and punch lists needed in the first release? If yes, must they support assignees, due dates, statuses, attachments, comments, reminders, and verification before completion?

6. Are daily logs required? If yes, what must they capture: labor, subcontractors, work completed, delays, deliveries, safety notes, weather, and photos?

7. Who creates daily logs, and who may view them?

8. Which workflows must work well on a phone? Is offline use required at job sites with poor service?

## 7. Finish Selections

1. Walk through a real finish selection from allowance setup through the client's final choice.

2. Can clients choose only from company-provided options, or may they request a custom option?

3. Can a selection require more than one choice or approval?

4. How should allowance overages and credits be handled?

5. Should an overage automatically create or update a change order, invoice, budget item, or QuickBooks record?

6. What should happen when a client misses a selection due date?

7. Do selections need vendor information, product links, specifications, photos, attachments, or lead times?

8. Can a completed selection be changed? If so, who can reopen it and what downstream records must change?

## 8. Company Size, Usage, and Administration

1. Approximately how many projects are active at one time?

2. Approximately how many users are expected in each role: admin, staff, accountant, client, and subcontractor?

3. Who besides admins may invite clients to a project? Should assigned staff be allowed to send and resend invitations?

4. Can staff access every company project, or only projects to which they are assigned?

5. Does the company need more detailed staff roles, such as project manager, superintendent, office manager, or read-only staff?

6. Are there required password, multi-factor authentication, session timeout, or audit-retention policies?

## 9. Scope and Launch Priorities

1. What are the three biggest problems with Buildertrend that this app must solve?

2. Which three workflows are most important for the first usable release?

3. Which current Buildertrend modules are used today, and how should each be classified?

| Module | Must have now | Later | Not needed | Notes |
| --- | :---: | :---: | :---: | --- |
| Client portal |  |  |  |  |
| Messaging/comments |  |  |  |  |
| Files/photos |  |  |  |  |
| Invoices |  |  |  |  |
| Online payments |  |  |  |  |
| QuickBooks integration |  |  |  |  |
| Scheduling |  |  |  |  |
| Tasks/punch lists |  |  |  |  |
| Change orders |  |  |  |  |
| Daily logs |  |  |  |  |
| Job costing/budgets |  |  |  |  |
| Estimates/proposals |  |  |  |  |
| Time clock |  |  |  |  |
| Finish selections |  |  |  |  |
| Warranties |  |  |  |  |
| CRM/leads |  |  |  |  |
| Subcontractor portal |  |  |  |  |

4. Is Duvally Residence the pilot project? What information must be loaded before the client can use it?

5. What result would make the pilot successful enough to continue or eventually offer the product as SaaS?

6. Is there a target date for the first live client use?

## Confirmed Decisions - Do Not Re-Ask Unless Something Has Changed

- The initial product is for one construction company, with a possible SaaS version later.
- The accounting platform is QuickBooks Online, and QuickBooks is the accounting source of truth.
- Required roles are admin, staff, accountant, client, and subcontractor; the subcontractor permissions remain undecided.
- Only admins and accountants may see company-level financial information.
- Staff may see project-level costs and financial information, but not company-level financials.
- Clients use a separate authenticated portal for their assigned projects.
- Clients need threaded messaging, document review and approval/decline, finish selections, and enough project financial information to make decisions.
- Messaging should notify both the company and the client.
- Authenticated approval is sufficient for the initial document-approval workflow.
- Change orders are required, and staff may prepare them.
- User accounts use email addresses rather than usernames.
- Company users can invite clients to access their projects; the exact internal roles allowed to send invitations still need confirmation.
