# Connecting to WhatsApp (Windows, beginner checklist)

This walks through wiring the bot up to a real WhatsApp conversation using Meta's free
**WhatsApp Cloud API test number**. It assumes Windows, PowerShell, and that you've already
run `uv sync` in this repo. Nothing here costs money, but step 9 flags the one thing that
expires.

## 1. Create a Meta developer account and app

1. Go to [developers.facebook.com](https://developers.facebook.com/) and log in with (or
   create) a Facebook account, then click **Get Started** to register as a developer.
2. Click **My Apps → Create App**.
3. Pick the **Other** use case (or **Business**), then choose the **Business** app type.
4. Name the app something like `saffron-lane-bot` and finish creation.

## 2. Add the WhatsApp product and get the free test number + temporary token

1. In your new app's dashboard, find **WhatsApp** in the product list and click **Set up**.
2. This opens **WhatsApp → API Setup**. Meta gives every app a free **test phone number**
   you can send/receive messages with immediately — no business verification needed yet.
3. On the same page you'll see a **Temporary access token**. Copy it — you'll need it below.
   Also copy the **Phone number ID** shown above the token.

## 3. Add your own phone as an allowed recipient

The test number can only message phone numbers you explicitly allow:

1. Still on **API Setup**, find **To** → **Manage phone number list**.
2. Add your own WhatsApp number (the one on your phone), then verify it with the code
   WhatsApp sends you.

## 4. Fill in `.env` and run the server

In the repo root:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set:

```
WHATSAPP_TOKEN=<the temporary access token from step 2>
WHATSAPP_PHONE_ID=<the phone number ID from step 2>
VERIFY_TOKEN=<any string you make up, e.g. letmein-123>
```

Leave `WHATSAPP_APP_SECRET` empty for now (see the note in step 6). Then run:

```powershell
uv run --env-file .env uvicorn orderbot.main:app --reload
```

Check the terminal output: on startup the app logs whether `WHATSAPP_TOKEN`,
`WHATSAPP_PHONE_ID`, `WHATSAPP_APP_SECRET` and `VERIFY_TOKEN` are set (as `true`/`false`, never
the actual values) and which LLM provider is active. Confirm the WhatsApp ones say `true`
before continuing.

## 5. Expose your local server with a free tunnel

Meta needs an HTTPS URL it can reach from the internet; `localhost` won't work. Of the common
free tunnels, **Cloudflare Quick Tunnel** needs no account, no card and no sign-up at all —
that's the one below. (ngrok's free tier also works but requires creating an ngrok account and
an authtoken first.)

1. Install cloudflared:
   ```powershell
   winget install --id Cloudflare.cloudflared
   ```
2. In a **second** PowerShell window (keep `uvicorn` running in the first one), run:
   ```powershell
   cloudflared tunnel --url http://localhost:8000
   ```
3. It prints a random URL like `https://random-words-1234.trycloudflare.com`. That's your
   public URL — copy it. Leave this window open; closing it kills the tunnel.

   Quick Tunnels are for testing only (no uptime guarantee, URL changes every time you restart
   `cloudflared`), which is fine for this demo.

## 6. Set the callback URL and verify token

1. Back in the Meta app dashboard, go to **WhatsApp → Configuration**.
2. Click **Edit** next to **Webhook**.
3. **Callback URL**: `https://<your-tunnel-domain>/webhook` (use the URL from step 5, with
   `/webhook` on the end).
4. **Verify token**: the same string you put in `VERIFY_TOKEN` in `.env`.
5. Click **Verify and save**. Meta sends a GET request with `hub.mode`, `hub.verify_token` and
   `hub.challenge`; the app checks the token matches and echoes the challenge back. If this
   fails, see the troubleshooting section below.
6. (Optional, more realistic) Set `WHATSAPP_APP_SECRET` in `.env` to the app's **App Secret**
   (found under **App settings → Basic**) and restart the server. This makes the webhook
   reject POSTs that don't carry a valid signature.

## 7. Subscribe to messages

1. Still on **WhatsApp → Configuration**, find **Webhook fields** and click **Manage**.
2. Subscribe to **messages**. You don't need any of the others for this bot.

## 8. Send "hi" from your phone

Open WhatsApp on the phone you verified in step 3, and message the test number shown in the
Meta dashboard. Send `hi`. Within a few seconds you should get a reply back from the bot.

## 9. The temporary token expires

The token from step 2 stops working after roughly a day — fine for testing, not for anything
you want running unattended. To get one that doesn't expire:

1. In **Business Settings** (gear icon in the Meta dashboard), go to **Users → System users**.
2. Click **Add**, give it a name, and set the role to **Admin**.
3. Under that system user, **Add assets** and assign your WhatsApp Business Account to it
   with full control.
4. Click **Generate new token**, select your app, grant the `whatsapp_business_messaging` and
   `whatsapp_business_management` permissions, and set expiration to **Never**.
5. Put that token in `WHATSAPP_TOKEN` in `.env` and restart the server.

Treat this token like a password — anyone who has it can send messages as your business. Some
of these options (and whether "Never" is actually offered) can depend on whether your Business
Account is verified; if a step above isn't available to you, that's Meta gating it behind
business verification, not something wrong with this repo.

## If the bot doesn't reply

Check these in order:

1. **Your server log** (the `uvicorn` terminal). Did a request hit `/webhook` at all? If
   nothing shows up, the problem is the tunnel or the callback URL, not the bot.
2. **The `cloudflared` window**. Confirm the tunnel is still running — Quick Tunnels drop if
   that process stops or your machine sleeps.
3. **Meta dashboard → WhatsApp → Configuration → Webhook**. Confirm the callback URL still
   matches your current tunnel URL — it changes every time you restart `cloudflared`.
4. **Meta dashboard → WhatsApp → API Setup → "To" list**. Confirm your phone number is still
   listed as an allowed recipient.
5. **Your server log again**, for errors logged by `orderbot.whatsapp` when sending a reply:
   it logs the HTTP status and Meta's error message (never the token), which usually says
   exactly what's wrong (expired token, number not in the allowed list, etc.).
6. If the webhook *verification* in step 6 failed, double check `VERIFY_TOKEN` in `.env`
   matches exactly what you typed into the Meta dashboard, and that the server was restarted
   after editing `.env`.
