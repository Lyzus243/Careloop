# WhatsApp Business integration

Vendors connect their own WhatsApp Business Account to Careloop and send bulk
messages to their customers. Messages go out from the **vendor's** number and
show the vendor's verified business name; Careloop's own number never appears.

## How messaging works

Meta only allows free-form text within 24 hours of a customer's last inbound
message. Careloop supports two campaign kinds:

| Kind | Who it reaches | How |
|---|---|---|
| **Template** | Everyone | An approved Meta template the vendor created in WhatsApp Manager. |
| **Custom** | Everyone, in two steps | Customers who messaged in the last 24 hours get the text immediately. Everyone else receives a short approved opener first ("Hi Ada, Bella's Boutique has a message for you. Reply YES to see it."), and the real message is delivered automatically when they reply. Parked messages expire after 7 days. |

Careloop creates the opener template on each vendor's account automatically at
connect time, so vendors never have to open Meta's tools to send a custom
message.

## Environment variables

```
META_APP_ID=                    # Meta app ID
META_APP_SECRET=                # Meta app secret
META_CONFIG_ID=                 # Embedded Signup configuration ID
META_GRAPH_VERSION=v21.0
WHATSAPP_WEBHOOK_VERIFY_TOKEN=  # any random string, also pasted into Meta
WHATSAPP_TOKEN_ENCRYPTION_KEY=  # Fernet key, see below
WHATSAPP_PHONE_PIN=000000       # 6-digit two-step verification PIN
WHATSAPP_ALLOW_MANUAL_CONNECT=false   # dev only, enables POST /connect/manual
```

Generate the encryption key once and keep it stable, or stored tokens become
unreadable and every vendor must reconnect:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The integration disables itself (and the dashboard hides the card) whenever
`META_APP_ID`, `META_APP_SECRET`, `META_CONFIG_ID` or the encryption key is
missing. Nothing else in the app is affected.

## Where each credential comes from

| Variable | Source | Already set? |
|---|---|---|
| `META_APP_ID` | Meta app → Settings → Basic | You fill this |
| `META_APP_SECRET` | Meta app → Settings → Basic | You fill this |
| `META_CONFIG_ID` | Facebook Login for Business → Configurations, **after Tech Provider approval** | Later |
| `META_GRAPH_VERSION` | Leave at `v21.0` | Done |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | Invented by you, also pasted into Meta | Generated |
| `WHATSAPP_TOKEN_ENCRYPTION_KEY` | Generated locally, never from Meta | Generated |
| `WHATSAPP_PHONE_PIN` | Any 6 digits you choose | Generated |

Only the three `META_` values require the Meta dashboard.

**You do not need `META_CONFIG_ID` to start.** The integration has two levels:

| With | What works |
|---|---|
| `META_APP_ID` + `META_APP_SECRET` | Sending, the send worker, webhooks, delivery receipts, replies, opt-outs. Connect via `scripts/wa_connect_dev.py`. |
| ...plus `META_CONFIG_ID` | The "Connect WhatsApp" button in Account Settings (Embedded Signup), so vendors self-serve. |

The dashboard hides the Connect button until the config ID is present, rather
than showing one that cannot work. Everything else can be built and tested with
the first two values, which are available as soon as the app exists.

## Step by step

### 1. Create a Meta Business Account

Go to <https://business.facebook.com/> and create a business portfolio if you do
not have one. Use the real registered name of the business.

### 2. Verify the business

Business Settings → Security Center → start **Business Verification**:
<https://business.facebook.com/settings/security_center>

You will upload a certificate of incorporation (CAC document for a Nigerian
company) and prove a business phone number or address. Approval usually takes a
few days.

Verification is required before real vendors can connect, and before you can
send more than 250 conversations a day. Start it now, and keep building with a
test number while it is pending.

### 3. Create the Meta app

Go to <https://developers.facebook.com/apps/> → **Create App**.

- Use case: **Other**
- Type: **Business**
- Link it to the business portfolio from step 1

### 3b. Choose "Become a Partner", not "Integrate with API"

The WhatsApp onboarding screen asks you to "Choose your integration type" and
preselects **Integrate with API**. Switch it to **Become a Partner**.

| Option | What it gives you | Right for Careloop? |
|---|---|---|
| Integrate with API | One WhatsApp number that *you* own, to message your own customers | No. Every vendor's message would come from a single Careloop number. |
| Become a Partner | Tech Provider status, which unlocks Embedded Signup so each vendor attaches their own WhatsApp Business Account | Yes |

Careloop is multi-tenant: vendors connect their own numbers and their customers
see the vendor's verified business name. That requires Tech Provider status.
`META_CONFIG_ID` only exists on the partner path, so the Connect button cannot
work without it.

After choosing Partner, the checklist becomes:

| Item | Do it when | Why |
|---|---|---|
| Step 1. Try it out | **Now** | Gives the test number, temporary token, phone number ID and WABA ID used by `/connect/manual`. Unblocks development immediately. |
| Step 2. Production setup | **Partly** | Meta marks it "Not required for partners", but two sub-steps do matter: **Configure Webhooks** and **Add payment**. Skip only "Register your WhatsApp phone number", since vendors bring their own. |
| Step 3. Business verification | **Now** | Five minutes to upload, then days of Meta review. Tech Provider status waits on it, so start it first. |
| Become Tech Provider | After verification clears | Produces the Embedded Signup configuration and covers app review. |

Do Step 1 and Step 3 on the same day. Everything else can proceed while
verification is pending, because manual connect does not depend on it.

**Add a payment method before testing sends.** Business-initiated messages are
billable. Without a payment method Meta rejects them with error 131031,
"Business Account locked", which reads like a suspension but is not one. The
account itself stays ACTIVE.

**Development mode limits webhooks.** While the Meta app is in Development mode,
only test webhooks fired from the app dashboard are delivered. Real delivery
receipts and real inbound messages do not arrive, even for app admins. The
opener-and-reply flow therefore cannot be verified end to end until the app is
switched to Live mode.

**Note on the Embedded Signup variant.** The dashboard launches Embedded Signup
through the Facebook JavaScript SDK with a `config_id` (see `connectWhatsApp()`
in the dashboard). Meta also offers a **Meta-hosted** Embedded Signup that you
redirect to instead. The backend is identical for both, since each returns an
auth code that `POST /api/whatsapp/connect` exchanges the same way. If your Tech
Provider setup yields a hosted URL rather than a configuration ID, only the
button handler in the dashboard needs changing.

### 4. Add the WhatsApp product

In the app dashboard sidebar, **Add Product** → **WhatsApp** → Set up.

This gives you a free test phone number and up to five allow-listed recipient
numbers, which is everything you need to develop before app review.

API Setup page: `https://developers.facebook.com/apps/1432363878960616/whatsapp-business/wa-dev-console/`

Copy three things from that page for local testing:
- **Temporary access token** (valid 24 hours)
- **Phone number ID**
- **WhatsApp Business Account ID**

Add your own mobile number under "To" so it can receive test messages.

### 5. Add Facebook Login for Business

**Add Product** → **Facebook Login for Business** → Set up.

Then open **Facebook Login for Business → Configurations** → **Create
configuration**:

- Login variation: **WhatsApp Embedded Signup**
- Assets: WhatsApp Business Accounts
- Permissions: `whatsapp_business_management`, `whatsapp_business_messaging`,
  `business_management`

Save it and copy the **Configuration ID** → that is `META_CONFIG_ID`.

**The WhatsApp Embedded Signup variation only appears once Tech Provider status
is granted**, which itself requires completed business verification. Before that
the wizard offers only "General" (confirmed 2026-09-28 on app 1432363878960616).

Do **not** create a "General" configuration as a stand-in. The wizard warns the
variation cannot be changed later, and a General config still yields an ID.
Setting it would make the Connect button appear while the popup never returns
WhatsApp account details, so the button would hang on "Connecting..." forever,
and the configuration could not be repaired. Leave `META_CONFIG_ID` empty until
the WhatsApp variation is selectable. The dashboard hides the button and
explains why, which is the correct state until then.

Reference: <https://developers.facebook.com/docs/whatsapp/embedded-signup>

### 6. Copy the App ID and App Secret

Settings → Basic:
<https://developers.facebook.com/apps/1432363878960616/settings/basic/>

- **App ID** → `META_APP_ID` (already set to `1432363878960616`)
- **App Secret** → `META_APP_SECRET`. Click **Show**, re-enter your Facebook
  password, and copy the 32-character hex string. Only app Admins can reveal it;
  if **Show** is missing, check Settings → Roles.

Set it without exposing it on screen or in shell history:

```bash
./scripts/set_env.sh META_APP_SECRET
```

While on that page, also fill in:
- Privacy Policy URL, for example `https://mycareloop.com.ng/privacy`
- Terms of Service URL
- App icon (1024x1024) and a category
- **App Domains**: `mycareloop.com.ng`

These are mandatory before app review.

### 7. Allow the dashboard domain for the JS SDK

Facebook Login for Business → **Settings**:

- Turn on **Login with the JavaScript SDK**
- **Allowed Domains for the JavaScript SDK**: `https://mycareloop.com.ng`
  (add `https://localhost:8001` too if you test the popup locally)

Without this the Connect button silently fails.

### 8. Point the webhook at your server

The webhook must be a public HTTPS URL on the **FastAPI host**, not the Vercel
static deployment.

WhatsApp → Configuration → Webhooks → Edit:

- Callback URL: `https://<your-render-host>/api/whatsapp/webhook`
- Verify token: the `WHATSAPP_WEBHOOK_VERIFY_TOKEN` value from your `.env`

Click Verify and Save. Careloop answers the challenge automatically, so it
should go green immediately as long as the server is running with that same
token.

Then **Manage** the subscribed fields and tick:
`messages`, `message_template_status_update`, `account_update`,
`phone_number_quality_update`.

For local testing, expose your machine with ngrok (<https://ngrok.com/download>):

```bash
ngrok http 8001
# then use https://<id>.ngrok-free.app/api/whatsapp/webhook as the callback URL
```

### 9. Fill in the three values and restart

Open `.env` and paste the App ID, App Secret and Configuration ID into the
`META_` lines. Restart the server and confirm these two lines appear:

```
WhatsApp Cloud API configured successfully
WhatsApp send worker started
```

If you instead see "WhatsApp integration disabled", one of the three values is
still blank.

### 10. Test before app review

With `WHATSAPP_ALLOW_MANUAL_CONNECT=true` you can connect using the temporary
token from step 4, skipping Embedded Signup entirely. The helper script handles
login and connection in one command:

```bash
python scripts/wa_connect_dev.py --token "EAAxxxxx..."
```

It prints the connected number, the intro-template status and your template
list. The Meta test token expires after 24 hours, so rerun it with a fresh one.

The script defaults to the test number's IDs; override them with
`--phone-number-id` and `--waba-id` for a different account.

Then in the dashboard: open a customer whose number is on your Meta allow list,
send a message, reply from the phone, and confirm the parked message arrives.

Meta only delivers to numbers on that allow list (five maximum), so add your own
number under **Recipient** on the API Setup page first.

### 11. Submit for app review, then go live

App Review → Permissions and Features:
`https://developers.facebook.com/apps/1432363878960616/app-review/permissions/`

Request `whatsapp_business_management` and `whatsapp_business_messaging`. Each
needs a screen recording showing a user connecting their account and sending a
message. Record the real flow from the Careloop dashboard.

When approved, flip the app from **Development** to **Live** using the toggle at
the top of the dashboard, and set `WHATSAPP_ALLOW_MANUAL_CONNECT=false` in
production.

Until the app is Live, only people with a role on the Meta app (Settings →
Roles) can complete Embedded Signup.

### 12. Set the same variables on Render

Render dashboard → your service → **Environment** → add all eight variables.
Use the exact same `WHATSAPP_TOKEN_ENCRYPTION_KEY` you have locally, or tokens
saved on one environment cannot be read on the other.

## Operational notes

- **Messaging limits.** An unverified business can start 250 conversations per
  24 hours. Larger campaigns fail with error 131056/130429; the compose modal
  warns above 250 recipients.
- **Phone format.** Customers need full international numbers (`+234 803 ...`).
  Local-format numbers are skipped and counted as "invalid number" in the
  campaign preview.
- **Token loss.** If a vendor removes the app, sends fail with code 190; the
  account flips to `error` and the settings card shows a Reconnect button.
- **Workers.** The sender claims work with `SELECT ... FOR UPDATE SKIP LOCKED`,
  which is real only on Postgres. Do not run multiple processes against SQLite.
