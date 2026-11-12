# Heritage controlled-launch checklist

This checklist covers the first Heritage rollout. QuickBooks linking, guided
onboarding, and subscription billing are intentionally outside this launch.

## Required before inviting Heritage users

- [ ] Set `APP_NAME=Heritage Construction Management` in Render.
- [ ] Confirm the final `SUPPORT_EMAIL` and set it in Render. Do not launch with
  the example address.
- [ ] Confirm the legal business name, contact email, mailing address,
  governing law, and effective date displayed by the legal pages.
- [ ] Confirm production email delivery by sending a real invitation and a
  password-reset email to an internal test address.
- [ ] Confirm the production PostgreSQL database has automated backups, then
  document and test the restore procedure.
- [ ] Confirm uploaded documents use storage that survives a Render deploy or
  service replacement. Do not rely on an ephemeral service filesystem.
- [ ] Confirm Sentry receives a production test event and that the event does
  not contain client data.
- [ ] Run migrations and static collection as part of the release.
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
- Stripe subscriptions remain disabled for Heritage. Keep
  `STRIPE_ENFORCE_SUBSCRIPTIONS=false`.

## First-week operating plan

- Use one pilot project before loading the full Buildertrend history.
- Export and retain the original Buildertrend files unchanged before importing
  any historical data.
- Record issues with the user, project, page, expected result, actual result,
  and time observed. Do not include passwords, tokens, or private document
  contents in issue reports.
- Treat defects in the agreed MVP separately from new workflow requests and
  Heritage-specific customization.
