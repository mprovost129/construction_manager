# Accountant and Windows operator onboarding

## Administrator

Open Company > Team & roles > QuickBooks setup. Choose **Invite accountant or
setup person**, which presets the Accountant role, View QuickBooks setup and
Update QuickBooks setup. Review the email and permissions, then send the invite.
Use a separate account for every person. Multiple people can access the shared
setup checklist. Do not give the Admin role merely to let someone prepare QuickBooks.

**View assigned project budgets** is a separate permission. Assign specific
projects on their Team pages if the accountant needs financial summaries.
Project administration and other existing role permissions remain separate.

Change an existing member's permissions in Team & roles > Edit role and permissions.
Unchecking permissions takes effect on the next request. Deactivating membership
removes the company's internal access. A user cannot alter their own role or
permissions; the site owner retains full access. Only company administrators or
the site owner can change another person's permissions. Existing role defaults
are retained until explicitly changed. Revoking an invitation prevents acceptance;
after acceptance, update the member's permissions instead.

## Accountant or setup person

1. Accept the invitation addressed to your own work email.
2. Set up two-step verification and privately save your recovery codes. Accounting
   and budget access requires verification under Heritage's security policy.
3. Open QuickBooks in the top navigation. Read the shared setup guide and save
   your portion of the checklist. Read-only users can review but cannot save.
4. Coordinate with the QuickBooks administrator and the Windows computer operator.
   These may be different people. Windows installation rights and QuickBooks
   administrator authorization are separate from Heritage 360 permissions.
5. Check the active company file's storage location and OneDrive synchronization
   status with IT. Do not move an open file. Prepare a current backup and a
   separate test company before testing.
6. Review existing integrated applications and install a compatible Intuit Web
   Connector from Intuit's official source if needed. Do not expose file shares
   or QuickBooks database ports to the internet.
7. Record customer/job structure, items/accounts, tax, numbering and credit rules.
   Do not enter credentials, license numbers, recovery codes or upload the QBW.

The supplied Product Information photo identifies QuickBooks Desktop Pro Plus
2024 R22P (64-bit) and host QB_TEENA_34. Confirm these on the installation computer.
The photo's OneDrive folder name does not establish whether synchronization is active.

## Connection status

This release supports preparation only. It does not generate a connection package,
provision connector credentials, enable the SOAP service, or transfer transactions.
Checklist ticks are operator attestations, not verified connection tests.

After the connection package and installation contract are implemented, verify the
intended test company without writing transactions. Implement and validate invoice
and credit transfers with that test company; accountant review is required before
live use. See [the integration plan](quickbooks_desktop_plan.md) for remaining work.

Official references:
- [Intuit installation instructions](https://quickbooks.intuit.com/learn-support/en-us/help-article/install-products/set-quickbooks-web-connector/L4Vp7VI44_US_en_US)
- [Intuit Web Connector architecture](https://static.developer.intuit.com/output_html/qbdesktop/docs/get-started/get-started-with-quickbooks-web-connector.html)

## Implementation and validation

Permissions are enforced on the server for every setup GET and POST and scoped to
the user's active company membership. Client and subcontractor roles cannot gain
accounting access, even if permission flags are present. Permission and setup changes
are audited without storing setup contents in the audit log. Revision checks prevent
one editor's old form overwriting a newer save. Organization row locks serialize
saves on PostgreSQL; local SQLite tests cover stale revision behavior, not actual
production lock contention. Browser forms retain global CSRF and Turnstile protection.

Validation on November 18, 2026: 585 automated tests passed; migration drift check
passed. Coverage includes delegated read-only/edit access, immediate revocation,
client/inactive/cross-company denial, administrator overrides, invite acceptance and
resend permission preservation, MFA and CAPTCHA enforcement, assigned-project budget
checks, and stale form rejection. Synthetic browser previews verified the onboarding
page and expanded per-person checkbox controls.
