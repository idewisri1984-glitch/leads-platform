# Manual outreach workflow

This workflow exports an approved email draft for a human to send in an external mail
client. The application does not send an email during either command.

## 1. Export the approved draft

```powershell
$env:DEBUG = "false"
uv run python -m app.cli.main agent email-draft export `
  --project-id 1 `
  --company-id 2 `
  --contact-id 3 `
  --email-draft-id 4 `
  --output text
```

Copy `TO`, `SUBJECT`, and the exact text between `BODY:` and the metadata separator into
the external mail client. Recheck the recipient and content before sending. Export is
read-only and may be repeated. A previously recorded draft is clearly reported as
`MANUALLY_SENT`.

For automation-safe inspection without sending:

```powershell
uv run python -m app.cli.main agent email-draft export `
  --project-id 1 --company-id 2 --contact-id 3 --email-draft-id 4 --output json
```

## 2. Send outside the application

Send exactly one email using the human-controlled mail client. Do not use the automatic
`send` command for the same draft. Never mark a draft as sent before the external mail
client has confirmed the human action.

## 3. Record the completed human action

```powershell
uv run python -m app.cli.main agent email-draft mark-sent `
  --project-id 1 `
  --company-id 2 `
  --contact-id 3 `
  --email-draft-id 4 `
  --confirm `
  --output text
```

`mark-sent` does not contact SMTP, AI, DNS, or social providers. It creates one durable
audit record only after `--confirm`. Duplicate confirmation and any draft that already
has an automatic delivery attempt are rejected. Once manually recorded, automatic
delivery is rejected before SMTP is invoked.
