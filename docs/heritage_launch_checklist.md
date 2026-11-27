# Heritage controlled-launch checklist

This checklist covers the first Heritage rollout. QuickBooks linking remains separate from the core launch. Desktop onboarding
details can now be collected from the administrator setup page.

## Required before inviting Heritage users

- [x] Final product name confirmed as **Heritage 360**. Set
  `APP_NAME=Heritage 360` in Render before launch.
- [ ] Confirm the final `SUPPORT_EMAIL` and set it in Render. Do not launch with
  the example address.
- [ ] Confirm the legal business name, contact email, mailing address,
  governing law, and effective date displayed by the legal pages.
- [ ] Confirm production email delivery by sending a real invitation and a
  password-reset email to an internal test address.
- [x] Confirm production PostgreSQL recovery and test the logical backup restore.
  The November 18 isolated database and document restore drill passed; see the
  [results and recovery limits](restore_drill_2026_11_18.md).
- [x] Confirm uploaded documents use durable private storage. R2 backup and
  recovery verified all 9 stored objects and all 7 database file references.
- [x] Obtain owner confirmation of private application-key recovery storage.
  Owner confirmed the Google Password Manager copy saved on November 18; no
  historical fallback keys are currently configured. Secret values were not inspected.
- [ ] Test recovery using the saved key and validate application-level recovery
  before a real database cutover.
- [ ] Confirm Sentry receives a production test event and that the event does
  not contain client data.
- [ ] Run migrations and static collection as part of the release.
- [ ] Verify `/operations/` access with Mike's developer account and confirm
  ordinary company users cannot open it. Test the **Open app** switch on desktop
  and mobile; see [Operations guide](operations.md).
- [ ] Configure external `/health/` monitoring and operational event retention
  after the Render and Better Stack accounts are ready.
- [ ] Create the Heritage company administrator and verify company and project
  permissions with a non-superuser account.
- [ ] Load one pilot project and complete the staff/client smoke test below.

## Staff and client smoke test

- [ ] Invite the single designated client account holder and accept the
  invitation from a separate browser session.
- [ ] Confirm the project blocks a second active client account holder.
- [ ] Confirm staff and client navigation on desktop and a phone-sized screen.
- [ ] Create and publish a daily log with a photo.
- [ ] Upload a document, add a new version, and complete a client decision.
- [ ] Create a selection and verify its Buildertrend-aligned status label and
  color.
- [ ] Submit and approve a change order, including the configured disclosure
  snapshot.
- [ ] Issue and download an invoice, including the configured disclosure
  snapshot.
- [ ] Send a project message and confirm email and in-app delivery.
- [ ] Turn one notification category off and confirm the user's preference is
  honored.
- [ ] Confirm client login, logout, page views, and editable actions appear in
  the audit history.
- [ ] Install the web app on a phone Home Screen and verify the mobile menu.

## Public QR documents

Public QR access is disabled for every document version by default. Before
staff enables it in production:

- [ ] Create a Cloudflare Turnstile widget for the production hostname.
- [ ] Set `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` in Render.
- [ ] Verify the QR link requires a successful human check in a private browser
  session.
- [ ] Confirm the shared file is intentionally safe for anyone possessing the
  QR code to view.
- [ ] Disable the share and confirm the old QR link no longer works.

## Deferred, with foundations preserved

- QuickBooks connection and accounting workflow acceptance remain deferred
  until Heritage confirms the company file, required records, and ownership of
  the accounting process.
- Guided onboarding remains deferred. Initial Heritage setup can be completed
  by an administrator using the existing company, project, people, and
  disclosure screens.
- Heritage is a dedicated company deployment. Application access billing stays
  disabled with `SUBSCRIPTION_BILLING_ENABLED=false`.

## First-week operating plan

- Use one pilot project before loading the full Buildertrend history.
- Export and retain the original Buildertrend files unchanged before importing
  any historical data.
- Record issues with the user, project, page, expected result, actual result,
  and time observed. Do not include passwords, tokens, or private document
  contents in issue reports.
- Treat defects in the agreed MVP separately from new workflow requests and
  Heritage-specific customization.
