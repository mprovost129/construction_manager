# Construction Manager Implementation Roadmap

Last updated: July 31, 2026

This is the authoritative implementation-status document for Construction Manager.
It translates the answers in [Construction Manager.md](./Construction%20Manager.md)
into delivered capabilities, remaining work, and release gates. Update this file in
the same change that materially changes a feature's status.

## Status definitions

- **Complete**: Implemented, exposed through the application, and covered by tests.
- **Partial**: A useful portion exists, but one or more confirmed requirements are missing.
- **Not started**: No operational implementation exists.
- **Deferred**: Intentionally excluded from the current release based on confirmed requirements.
- **Blocked**: Work cannot be represented as production-ready until the listed dependency or gate is satisfied.

Configuration keys, copy, mock screens, and legal descriptions do not make an
integration operational. A feature is not complete until its end-to-end behavior is
implemented and verified.

## Executive status

| Area | Status | Current position |
| --- | --- | --- |
| Authentication and company administration | Complete | Email authentication, invitations, roles, project assignments, password reset, and startup superuser bootstrap exist. |
| Project and client portal foundation | Complete | Projects, scoped access, dashboard/action center, messaging, activity history, and client invitations exist. |
| Documents and client uploads | Complete | Private documents, versions, downloads, configurable-approval-count decisions, client uploads, and email notifications exist. |
| Change orders | Partial | Draft, submission, configurable multi-approval decisions, edit, optional line items, revise/replace with a revision chain, and void (including approved orders) exist; automated financial reversal on void and a formal cost-code catalog do not (blocked on APP-1/APP-2). |
| Finish selections | Complete | Options, allowance math, publishing, client choice, overage flag, reopening, package grouping for multi-area choices, vendor/link/spec/image/lead-time option metadata, client custom-option requests routed to a change order, credit disposition tracking, and manual + scheduler-driven overdue reminders exist. |
| Schedule | Partial | Internal milestone/calendar workflow exists; dependencies, recurrence, and external calendar integration do not. |
| Notifications | Partial | Transactional email exists with project-level recipient preferences; per-event settings, reminders, and digest delivery do not. |
| Project pricing and financials | Not started | No product/material/labor/commission pricing engine, job-costing ledger, estimate, proposal, or project financial rollup exists. |
| Invoices and payment visibility | Not started | No application invoice model, client invoice portal, balance view, or payment-status workflow exists. Online payment is deferred. |
| QuickBooks Online | Blocked | Environment configuration and policy language exist, but no OAuth, tokens, API calls, synchronization, webhooks, or CDC exists. |
| Tasks and punch lists | Not started | Confirmed as required, but no models or workflow exist. |
| Two-factor authentication | Not started | Confirmed as optional per user/admin policy, but not implemented. |
| Public legal pages | Complete with launch action | Public EULA and privacy pages exist; real legal entity values and counsel review are still required before production submission. |
| Deployment foundation | Partial | Docker, Render-oriented startup, migrations, static assets, security settings, and environment examples exist; persistent uploads, availability, backups, and operations need production validation. |

## Confirmed product decisions

These decisions govern remaining implementation work:

- QuickBooks is the accounting source of truth.
- Exchange customers, invoices, credit memos, and payments in both directions.
- Invoices may originate in Construction Manager.
- An approved change order is sent or marked ready for QuickBooks when invoiced, not merely when approved.
- Admins resolve synchronization errors and conflicts.
- Construction projects map to QuickBooks customers; do not depend on QuickBooks Jobs.
- Begin with active projects and new transactions; no historical migration is required.
- QuickBooks sandbox credentials are strictly for development. Production must use production credentials and companies.
- Clients may view/download invoices, balances, and payment status, but cannot pay online in the first release.
- One authenticated client approval is sufficient for change orders by default; the required
  approval count is configurable per change order (and per document) for cases that need more.
- Clients can upload project files and photos; assigned internal users are notified.
- Email is sufficient for initial notifications.
- Internal users see only assigned projects, except company administrators who retain company-wide control.
- Supported internal roles are Admin, Manager, Project Manager, Staff, Office Manager, Real Estate Agent, and Accountant.
- Clients do not use the project schedule. Admins, managers, and project managers operate it.
- Selection overages are flagged and suggest a change order; they do not automatically create one.
- Completed selections can be reopened by authorized internal users, not by clients.
- Subcontractor login, subcontractor financial access, daily logs, and online payments are deferred.

## Delivered baseline

### Authentication, roles, and access

- Custom email-based user model and password-reset flow.
- Company and project invitation acceptance, resend, revoke, and access restoration.
- Company roles and project-specific internal assignments.
- Separate manage-project, manage-client, and notification permissions.
- Project-scoped queries and authorization checks across portal modules.
- Idempotent environment-backed Django superuser bootstrap for container startup.
- Brute-force login protection and production session/security configuration.

### Project portal

- Portfolio dashboard, project cards, search, status filtering, and action center.
- Project activity timeline and CSV export.
- Client-visible project detail and explicit project membership.
- Threaded project messages with replies and status management.
- Email notifications for implemented workflows.

### Documents and photos

- Private file storage, validated extensions, and configurable 25 MB limit.
- Document categories, client visibility, version history, and secure download authorization.
- Client approve/decline decisions and activity audit events.
- Configurable required-approval count per document, resolved from a per-client decision log.
- Dedicated client upload workflow for documents and images.
- Assigned internal-recipient notification behavior.

### Change orders

- Sequential project numbering.
- Draft creation/editing, price delta, cost delta, schedule delta, and reason fields.
- Optional itemization by category (product, material, labor, subcontractor, commission/markup,
  tax, allowance, other) with a free-text cost-code tag; totals recalculate automatically from
  line items whenever any exist.
- Client submission, authenticated approve/decline, comment, and notifications.
- Configurable required client approval count, resolved from a per-client decision log (a single
  approval remains sufficient by default).
- Revise-in-place and create-replacement actions after a decline, with a visible, linked revision
  chain between the original and its replacement.
- Void workflow and audit history, extended to approved change orders with a mandatory reason and
  a `requires_financial_reversal` flag for manual QuickBooks handling.
- QuickBooks handoff language aligned to the invoice event.

### Finish selections

- Sequential numbering, allowance, due date, option price/cost, recommendation, and ordering.
- Draft, publish, client choice, selected, reopened, and voided states.
- Client-facing allowance variance with internal-only estimated cost and margin.
- Overage flag and prefilled change-order call to action.
- Reopening by an authorized internal user with client re-notification.
- Optional `SelectionPackage` grouping so related area choices (e.g. all Kitchen selections)
  share a package view, mirroring Buildertrend's Group-of-Choices structure; each choice keeps
  its own allowance, options, and client pick.
- Option vendor, product link, specification, lead time, image, and attachment fields, served
  through authenticated download/inline views scoped to selection visibility.
- Client custom-option requests, with staff review routing directly into a prefilled change-order
  draft (no change order is created automatically).
- Credit disposition tracking (apply elsewhere, return at closing, retain as margin) for
  under-allowance choices, settable by authorized internal users and reset on reopen.
- Manual "Send reminder" action plus a `send_overdue_selection_reminders` management command for
  overdue open selections; the command needs an external scheduler in production (see Production
  blockers).

### Schedule

- Internal milestone list and calendar views.
- Start/end dates, status, notes, ordering, create, and update workflows.
- Schedule is unavailable to clients, accountants, and subcontractors.

### Legal and deployment

- Public `/legal/eula/` and `/legal/privacy/` pages with footer links.
- Environment-driven legal entity, contact, address, governing law, and effective date.
- Development/production environment examples, production HTTPS controls, database SSL option, and console-first logging.
- Docker static build settings, runtime migrations, and superuser bootstrap.
- Current automated baseline: 155 passing tests, Ruff clean, Django checks clean, migration check clean, and `collectstatic` passing as of this update.

## Partial feature gaps

### Change orders

- Voiding an approved change order sets a `requires_financial_reversal` flag for staff to action
  manually; there is no automated accounting reversal because no invoicing/financial ledger exists
  yet (blocked on [Phase APP-1](#phase-app-1-project-pricing-and-financial-ledger---p0) and
  [APP-2](#phase-app-2-invoices-and-client-visibility---p0)). Revisit once invoices exist so a void
  after invoicing can be rejected or trigger a credit memo instead of only a flag.
- Change order line items use a free-text cost-code tag, not a formal pricing/cost-code catalog;
  that catalog is also APP-1 scope.

### Finish selections

- Support multiple choices for different areas within one selection package.
- Add vendor, product URL, specification, image/attachment, and lead-time fields.
- Support client custom-option requests that route to a proposed change order.
- Add credit disposition: apply elsewhere, return at closing, or retain as project margin.
- Add overdue reminders with automatic and authorized manual email actions.

### Schedule and notifications

- Restrict schedule creation and updates to the confirmed Admin, Manager, and Project Manager policy; the current assigned-internal-user access is broader.
- Add milestone dependencies and dependency-aware date changes.
- Add recurring schedule items.
- Add external calendar integration.
- Add per-event notification settings rather than one project-wide email preference.
- Add immediate versus digest delivery and overdue reminder preferences.

### Mobile experience

- Complete a phone-size workflow audit for client approvals, uploads, messaging, selections, and field use.
- Define whether offline access is required; the interview answer does not provide a testable offline requirement.

## Production blockers

The following must be resolved before representing the application as production-ready.

### Hosting and data operations

- Replace container-local private document storage with durable object storage. Render container files are not a safe system of record for customer documents.
- Confirm persistent production PostgreSQL, automated backups, restore testing, and retention.
- Confirm a production Redis service or remove runtime dependency on it.
- Configure and verify SMTP delivery, sender-domain authentication, bounce handling, and support/privacy mailboxes.
- Validate the selected hosting tier against Intuit's availability expectations; free-tier spin-down must not be represented as continuous production availability.
- Add production error monitoring, health checks, alerting, and an incident-response procedure.
- Establish an operational privacy-request, data-export, QuickBooks-disconnect, and deletion procedure matching the published Privacy Policy.

### Security and legal

- Implement optional two-factor authentication, authenticator-app enrollment, recovery codes, admin enforcement, and audit events.
- Store QuickBooks OAuth tokens encrypted at rest and keep production secrets only in the Render secret store.
- Configure real `LEGAL_*` values and obtain legal review of the EULA and Privacy Policy.
- Add a public support/help route and documented response process.
- Perform dependency, vulnerability, access-control, upload, and authorization testing before external users are admitted.

## QuickBooks Online roadmap

Current questionnaire truth as of this update:

| Question | Accurate answer today |
| --- | --- |
| API calls per customer | Zero |
| Handles QBO edition feature gains/losses | No |
| Uses webhooks | No |
| Uses CDC | No |
| Operational connect/reconnect URL | No |
| Operational disconnect URL | No |
| Operational OAuth callback | No |

Do not change an answer to "Yes" until the corresponding acceptance criteria below pass.

### Phase QB-1: Connection and security foundation - P0

- Add one or more QuickBooks company connections per organization, including realm ID, environment, connection status, granted scopes, token expiry, and audit timestamps.
- Encrypt access and refresh tokens at rest and prevent them from appearing in logs, admin pages, exceptions, or exports.
- Implement OAuth state generation/validation and authorization-code exchange.
- Implement authenticated connect/reconnect and callback routes.
- Implement in-app disconnect through Intuit's revoke endpoint.
- Implement a public disconnect landing page that confirms disconnection and explains reconnection.
- Add the exact production URLs to Intuit only after they return the intended response over HTTPS.
- Prevent sandbox credentials or realm IDs from operating under production settings.

Acceptance gate:

- Connect, reconnect, token refresh, revoke, expired-token, denied-consent, invalid-state, and multiple-company scenarios pass automated and sandbox tests.
- No token or client secret is exposed in logs or responses.
- The UI clearly displays connection state and the connected QuickBooks company.

### Phase QB-2: Capability and mapping layer - P0

- Read company/subscription information and detect supported capabilities without hard-coding an edition assumption.
- Re-evaluate capabilities after reconnect and relevant API errors.
- Gracefully disable unsupported operations while preserving existing local data.
- Define stable mappings for Organization/Project to QuickBooks company/customer without using QuickBooks Jobs.
- Define external IDs, sync tokens, tombstones, last-synced values, and ownership for every synchronized entity.
- Define conflict rules that preserve QuickBooks as accounting source of truth while allowing local invoice origination.

Acceptance gate:

- Sandbox tests cover Simple Start, Essentials, Plus, and Advanced capability differences or representative error simulations.
- A downgrade never deletes local data or traps the user in a failing sync loop.

### Phase QB-3: Entity synchronization - P0

Implement in this order:

1. Customers
2. Invoices
3. Credit memos
4. Payments

For each entity:

- Implement both-direction create/update behavior where supported by the confirmed workflow.
- Add idempotency, sync-token/concurrency handling, pagination, retry with backoff, and rate-limit handling.
- Record durable sync attempts, results, external IDs, and sanitized error details.
- Provide an admin error queue with retry and resolution actions.
- Ensure change orders become eligible for handoff only through the invoice workflow.
- Add a manual Sync action plus a safe scheduled/event-driven path.

Acceptance gate:

- Create, update, delete/void, duplicate delivery, stale version, partial failure, retry, and conflict tests pass for every entity.
- Reconciliation proves local and QuickBooks totals agree for test scenarios.

### Phase QB-4: Change detection and reconciliation - P1

- Choose and document the production change-detection design before answering Intuit's webhook/CDC questions.
- Preferred design: verified webhooks for prompt notification plus CDC or targeted queries for scheduled reconciliation.
- If using webhooks, implement a public endpoint, Intuit signature verification, replay protection, idempotent queueing, fast acknowledgement, and safe processing retries.
- If using CDC, persist high-water marks, respect the 30-day look-back constraint, split queries to stay below response limits, and process deletions.
- Add a nightly reconciliation job and manual recovery action.

Acceptance gate:

- Missed, delayed, duplicated, reordered, and replayed events do not lose or duplicate accounting records.
- Operational metrics report API call volume, success rate, throttling, sync lag, and unresolved errors per company.

## Application roadmap after the QuickBooks foundation

### Phase APP-1: Project pricing and financial ledger - P0

- Add a project pricing catalog and line items for products, materials, labor, commission/markup, tax, allowances, and cost codes.
- Add estimate/proposal generation and client-visible approval where required.
- Calculate base project total, selection overages/credits, change orders, invoices, payments, and remaining balance without treating estimates as accounting truth.
- Add staff financial views and client-safe financial summaries.
- Define rounding, tax, void/reversal, and audit rules.

### Phase APP-2: Invoices and client visibility - P0

- Add local invoice drafts originating from approved selections or change orders.
- Add invoice status, immutable numbering rules, line items, totals, balances, credit application, and QuickBooks mapping.
- Let clients view invoice details, download invoice documents, see balances/payment status, and ask questions.
- Do not add online payment collection in this release.

### Phase APP-3: Tasks and punch lists - P1

- Add assignees, due dates, statuses, attachments, comments, reminders, and completion verification.
- Reuse project-scoped access and notification settings.
- Optimize create/update/complete/photo workflows for phones.

### Phase APP-4: Remaining workflow depth - P1

- Complete selection metadata, credit disposition, and reminders.
- Complete change-order line items and revision chains.
- Complete schedule dependencies, recurrence, and external calendar support.
- Complete configurable multi-client approval rules.
- Add notification event preferences and digests.
- Add CRM customer/contact management without a lead pipeline.

## Deferred scope

The following are intentionally outside the current release unless requirements change:

- Subcontractor portal and subcontractor financial visibility.
- Daily logs.
- Online payment processing.
- Historical Buildertrend or QuickBooks import.
- Formal e-signatures/DocuSign; authenticated approval remains sufficient initially.
- QuickBooks Desktop integration.
- Payroll integration.
- QuickBooks Self-Employed/Solopreneur and Intuit Enterprise Suite support.

## Release gates

### Internal pilot gate

- All migrations apply cleanly to a production-like database.
- Full automated suite, Ruff, Django system checks, and `collectstatic` pass.
- Durable document storage, SMTP, backups, monitoring, and restore procedures are verified.
- Permission matrix is manually tested for every role.
- Legal environment values are real and public pages return HTTP 200 without authentication.
- No known critical/high security findings remain.

### QuickBooks sandbox gate

- QB-1 through QB-4 acceptance criteria pass with sandbox credentials only.
- Supported entities reconcile across create/update/void/error scenarios.
- Connect, reconnect, disconnect, callback, webhook/CDC, and edition-change behaviors are documented for reviewers.
- Questionnaire answers match measured implementation behavior.

### Production/Intuit submission gate

- Production credentials are not requested or enabled as a substitute for unfinished integration work.
- Hosting meets Intuit availability and security expectations.
- Production OAuth URLs, EULA, Privacy Policy, support route, and disconnect instructions are public and stable.
- Security assessment evidence and incident-response contacts are ready.
- A controlled first production company can be connected, monitored, reconciled, and disconnected without developer intervention.

## Maintenance rule

Every feature change must update the relevant status, acceptance criteria, and current
questionnaire truth in this document. "Complete" requires code, user-facing behavior,
authorization coverage, tests, and operational support--not configuration alone.
