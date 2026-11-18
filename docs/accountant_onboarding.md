# Accountant and Windows operator onboarding

## Administrator

Open Company > Team & roles > QuickBooks setup. Choose **Invite accountant or
setup person**, which presets the Accountant role, View QuickBooks setup and
Update QuickBooks setup. Review the email and permissions, then send the invite.
For the Windows installer, also select **Manage QuickBooks connection** and, if needed,
**Request QuickBooks connection checks**. These are separate from checklist editing
and are not automatically granted by the accountant invitation preset. Admins
have these permissions by default unless explicitly revoked.
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

## Install and verify the shared connection

1. In Heritage, open **QuickBooks > Open connection setup** on the computer that
   can open the intended company file. The installer needs View QuickBooks setup
   and Manage QuickBooks connection. A person who only requests checks needs
   View QuickBooks setup and Request QuickBooks connection checks.
2. Open the intended file in QuickBooks as its administrator. Press F2 and copy
   the exact company name and full .QBW location into the setup form. The website
   stores a path fingerprint, not the path or company file. This registration is
   bound to that location; a later file move or switch from a test copy requires
   site-owner review. Do not silently replace or move the registered file.
3. Choose a unique 16–64 character Web Connector password, save it privately,
   confirm it, and enter your own current Heritage password. Confirm the backup
   and storage preparation and choose **Prepare connection setup**. The connector
   password is stored as a one-way hash and is never displayed again.
4. Download the .qwc file. In Intuit Web Connector on that same computer, choose
   **Add an Application** and select it. Review and approve the Heritage app in
   QuickBooks, with no personal-data access. Web Connector records application
   registration metadata; Heritage only requests company verification.
5. Enter the dedicated password in the Heritage row in Web Connector, select
   that row, and choose **Update Selected**. Keep **Auto-Run** off. Leave the
   intended company open during this initial verification.
6. Back in Heritage, **Refresh status**. **Company verified** means the latest
   check matched the location fingerprint, name and registered FileID. It does
   not mean a continuous connection or that accounting transfers are enabled.
   A failure clears verification; correct the issue, request a new check in
   Heritage, and run Update Selected again.

One Windows computer runs the shared connection. Authorized staff use their own
Heritage accounts to see status or queue a check; the website cannot remotely
start Web Connector. They do not each install another connector. Revoking a
person's website access does not revoke the shared computer credential. If that
person knew the connector password, an authorized manager should disconnect and
reset it. Disconnect invalidates the shared password and all current tickets.
Reset for the same file preserves registration IDs and reuses the existing Web
Connector row with the new password.

This release verifies the company only. Invoice, credit, customer and payment
transfers are unavailable. Automated tests use synthetic data; installation and
approval in the accountant's actual QuickBooks/Web Connector remain to be tested.
A copied or restored company can retain its registration ID, so this check alone
must not authorize future financial writes. Transfers require mapping,
reconciliation, test-company validation and accountant review first.

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

Validation on November 18, 2026: 602 automated tests passed. Coverage includes
configuration, reauthentication, separate view/manage/check permissions, client
and cross-company denial, CSRF/Turnstile/MFA, credential/session revocation,
revisions, duplicate-check suppression, private QWC downloads, file identity,
SOAP sequencing and existing application behavior. The connection form was
reviewed in a synthetic local browser preview. An independent Zeep SOAP client
parsed the WSDL and completed a synthetic version/authentication/company-check/
close/no-work exchange successfully. Migration drift check passed. Actual Windows QuickBooks
interoperability is pending; do not describe this as a completed company sync.
