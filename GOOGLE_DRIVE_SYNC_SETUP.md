# Google Drive sync setup (Render) — step-by-step

This is the operator guide for the **Admin → Accounting → Google Sheets**
integration: the app writes confirmed orders straight into the store owner's
Google Drive (the ITEMFLOW reference workbook's **NGN** and **FCFA** tabs, plus
its own Naira / CFA ledgers).

Read it top to bottom once. After that you only ever repeat **Part 3** when you
want to confirm the sync still works.

---

## Part 1 — Google Cloud Console: get `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`

You need a normal Google account that can open <https://console.cloud.google.com>.
Use the **owner's** Google account (the Drive that should hold the ledgers).

### 1. Create (or pick) a project

1. Open <https://console.cloud.google.com/projectcreate>.
2. **Project name**: `Jaura Store` (any name works).
3. Leave **Location** as “No organization”, click **CREATE**.
4. Wait a few seconds, then make sure the project selector at the top of the
   page shows `Jaura Store`.

### 2. Enable the two APIs

Do this twice, once per API:

1. Open <https://console.cloud.google.com/apis/library/sheets.googleapis.com> →
   click **ENABLE** (Google Sheets API).
2. Open <https://console.cloud.google.com/apis/library/drive.googleapis.com> →
   click **ENABLE** (Google Drive API).

Both must read **Manage → API enabled**.

### 3. Fill in the OAuth consent screen

1. Open **APIs & Services → OAuth consent screen**
   (<https://console.cloud.google.com/apis/credentials/consent>).
2. **User type**: choose **External** → **CREATE**.
3. **App information**
   - App name: `Jaura Store Accounting`
   - User support email: `jaurastore@gmail.com`
   - Developer contact information: `jaurastore@gmail.com`
4. Click **SAVE AND CONTINUE** on every remaining step
   (**Scopes** → leave the defaults, **Test users** → see the next step,
   **Summary** → **BACK TO DASHBOARD**).
5. Still on the **Test users** step, click **+ ADD USERS** and add the Google
   address that will connect the Drive (for example `jaurastore@gmail.com`).
   While the app is in “Testing”, only listed test users can connect.

> **Publishing is optional.** In *Testing* mode Google refresh tokens expire
> after 7 days, so the Accounting desk will ask you to reconnect about once a
> week. If you want a permanent connection, press **PUBLISH APP** on the
> consent screen (no Google verification review is needed for these scopes for
> personal use; Google may show an “unverified app” warning that you can accept
> with *Advanced → Go to Jaura Store Accounting*).

### 4. Create the OAuth client ID

1. Open **APIs & Services → Credentials**
   (<https://console.cloud.google.com/apis/credentials>).
2. **+ CREATE CREDENTIALS → OAuth client ID**.
3. **Application type**: **Web application**.
4. **Name**: `Jaura Store web`.
5. **Authorized redirect URIs** → **+ ADD URI**, and add **both** (exactly,
   no trailing slash):

   ```
   https://jaurastore.com.ng/api/admin/accounting/google/callback
   http://localhost:8080/api/admin/accounting/google/callback
   ```

   The first is production, the second is for a local run. If you also serve
   `www.jaurastore.com.ng`, add that host's callback too.
6. Click **CREATE**. A dialog shows the two values you need:
   - **Client ID** → `GOOGLE_CLIENT_ID` (ends in `.apps.googleusercontent.com`)
   - **Client secret** → `GOOGLE_CLIENT_SECRET` (starts with `GOCSPX-`)

   Keep this dialog open; you can always re-open it later under
   **Credentials → OAuth 2.0 Client IDs → Jaura Store web**.

---

## Part 2 — Render: Environment Variables

1. Open <https://dashboard.render.com> → the **jaurastore** web service.
2. Go to **Environment → Environment Variables**.
3. Add/confirm every row in this table, then click **Save Changes**
   (Render redeploys automatically; if it does not, press **Manual Deploy →
   Deploy latest commit**).

### Keys and values

| Key | Value | Notes |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` | *(paste the Client ID from Part 1)* | Secret-ish; keep it out of git. |
| `GOOGLE_CLIENT_SECRET` | *(paste the Client secret from Part 1)* | Secret. Never share or commit. |
| `GOOGLE_REDIRECT_URI` | `https://jaurastore.com.ng/api/admin/accounting/google/callback` | Must match Part 1 step 4 **character for character**. |
| `GOOGLE_SHEET_ID` | `1GnBgXl-VNoRzV-jiz4qCeb_BKzs31_Fu` | The ITEMFLOW workbook whose **NGN** / **FCFA** tabs receive pushed batches. A value saved on the accounting desk wins over this. |
| `GOOGLE_SHEETS_TOKEN_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` | Encrypts the stored refresh token. Set once and never change it, or the saved Google grant cannot be decrypted. |
| `SITE_ORIGIN` | `https://jaurastore.com.ng` | Used for canonical links and as the callback fallback. |
| `SECRET_KEY` | *(generate)* | Must stay stable across deploys. |
| `ADMIN_MASTER_PASSWORD` | *(private)* | Primary admin password. |
| `ADMIN_BOOTSTRAP_PASSWORD` | *(private)* | Fallback admin password. |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | *(from Supabase)* | Ledger settings, batches and tokens are stored here. |

`GOOGLE_SHEET_ID` is already declared in `render.yaml`, so a Blueprint sync
(`render.yaml` → **Sync**) fills it in for you; the other Google values are
marked `sync: false` on purpose and must be typed into the dashboard.

### Sharing the workbook with the connected account

The app writes as the **account that completes the OAuth connect** (shown on
the Accounting desk). The simplest setup is to connect with the same Google
account that owns the ITEMFLOW workbook. If they differ, open the workbook →
**Share** → add the connected address as **Editor**.

---

## Part 3 — Test and confirm that the Drive sync is working

1. Wait for the Render deploy to finish (**Events** shows *Live*), then open
   <https://jaurastore.com.ng/admin/accounting> and sign in.
2. In the **Your ledgers** strip, click **Connect Google Sheets** and pick the
   owner's Google account. Approve the consent screen (the app asks for Drive +
   Sheets access so it can write to your own ITEMFLOW workbook). You come back
   to the accounting page with a green **Drive connected · you@gmail.com** chip
   and three buttons: **Open reference sheet**, **NGN ledger**, **FCFA ledger**.
3. Click **✓ Test Google sync**. Expected result:
   - a green toast **“Google Drive sync is working.”**, and
   - the checks it just ran are logged in the browser console
     (only shown when something fails).
   The test is read-only: it verifies the OAuth client, refreshes the token if
   needed, reads both ledgers, and reads the reference workbook’s tabs
   (creating nothing, sending nothing).
4. **Prove a real write.** On the staging queue, pick one confirmed NGN order
   and press **⬆ Push … to Google Sheet**. Open the reference workbook: the row
   appears on the **NGN** tab (order id, customer, items, revenue, supplier
   cost, transport, net profit, batch). Pushing the same order twice updates the
   same row instead of duplicating it.
5. Confirm the **Dashboard → Accounting** summary cards fill in from the sheet
   (📅 Week / Month / Year filter), and that **Open reference sheet** opens the
   workbook whose id you set as `GOOGLE_SHEET_ID`.

If all five steps pass, direct Google Drive syncing is live.

### API equivalent (for a scripted check)

```
GET /api/admin/accounting/google/verify      # with the admin session cookie
```

returns `verification.ok = true` and per-step details (`oauth-client`,
`owner-token`, `NGN-ledger`, `CFA-ledger`, `reference-sheet`).

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Accounting desk says *“Google Sheets setup is required on the server.”* | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` are missing or the deploy has not picked them up. | Re-check the Render variables, then redeploy. |
| `redirect_uri_mismatch` on the Google screen | The callback in Google differs from `GOOGLE_REDIRECT_URI` (extra slash, `www`, or `http`). | Make them identical in **Credentials → OAuth client → Authorized redirect URIs**. |
| *“Reconnect Google Sheets to restore access to the saved ledgers.”* | `GOOGLE_SHEETS_TOKEN_KEY` (or `SECRET_KEY`) changed, or the app is still in Google *Testing* mode and the 7-day refresh token expired. | Set a stable `GOOGLE_SHEETS_TOKEN_KEY`, click **Connect Google Sheets** again, and consider publishing the consent screen. |
| Connect returns `?google=state-error` | The sign-in link was opened in another tab/host, or it sat for over 15 minutes. | Start the connect flow again from `/admin/accounting`. |
| `403` / *“The caller does not have permission”* when pushing | The connected account cannot edit the ITEMFLOW workbook. | Share the workbook with the connected address as **Editor** (Part 2). |
| Pushes work but the ITEMFLOW tabs are empty | The batch went to the app-created ledgers because the reference id was cleared on the desk. | Set **Reference spreadsheet** back to `1GnBgXl-VNoRzV-jiz4qCeb_BKzs31_Fu`, or clear the field to fall back to `GOOGLE_SHEET_ID`. |
| *“Set GOOGLE_SHEETS_TOKEN_KEY (or a stable SECRET_KEY) before connecting”* | Production is using a random per-boot `SECRET_KEY`. | Set `GOOGLE_SHEETS_TOKEN_KEY` (recommended) or a fixed `SECRET_KEY`, then redeploy. |

> Security notes: the refresh token is encrypted with Fernet before it is
> written to Supabase, and no Google credential is ever sent to the browser.
> Revoke the grant at any time from the owner's Google account
> (**Security → Third-party apps**) or by reconnecting on the accounting desk.
