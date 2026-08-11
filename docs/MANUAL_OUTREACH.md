# Manual outreach workflow

This workflow lets a human review and send an approved email draft through Gmail or
another external mail client. **The application does not send email in this workflow.**
SMTP configuration is **not required**. Automatic SMTP delivery is deferred to a future
version.

All examples below are valid Windows PowerShell commands. Replace the example IDs and
sender details with values from your local database.

## 1. Find the authoritative records

List projects and companies, then list contacts for the selected company:

```powershell
uv run python -m app.cli.main project list
uv run python -m app.cli.main company list
uv run python -m app.cli.main contact list --company-id 2
```

Email draft generation also requires an existing Lead and Task. List them and select the
records that belong to the same project, company, and contact:

```powershell
uv run python -m app.cli.main lead list
uv run python -m app.cli.main task list
```

## 2. Generate a draft

Generation uses the configured AI draft provider. It persists a DRAFT; it does not send
email.

```powershell
uv run python -m app.cli.main agent email-draft generate `
  --project-id 1 `
  --company-id 2 `
  --contact-id 3 `
  --lead-id 4 `
  --task-id 5 `
  --sender-name "Alex Sender" `
  --sender-company "Example Company" `
  --purpose "Introduce our service" `
  --tone professional `
  --language en `
  --output text
```

Record the returned draft ID.

## 3. Show and review the draft

```powershell
uv run python -m app.cli.main agent email-draft show `
  --project-id 1 `
  --company-id 2 `
  --contact-id 3 `
  --draft-id 6 `
  --output text
```

Verify the recipient, subject, body, sender identity, and context before approval.

## 4. Approve the reviewed draft

```powershell
uv run python -m app.cli.main agent email-draft approve `
  --project-id 1 `
  --company-id 2 `
  --contact-id 3 `
  --draft-id 6 `
  --yes `
  --output text
```

## 5. Export the copy-ready email

```powershell
uv run python -m app.cli.main agent email-draft export `
  --project-id 1 `
  --company-id 2 `
  --contact-id 3 `
  --email-draft-id 6 `
  --output text
```

Copy `TO`, `SUBJECT`, and the exact text between `BODY:` and the metadata separator into
the external mail client. Export is read-only and may be repeated.

## 6. Send manually outside the application

Send exactly one email using Gmail or another human-controlled mail client. Do not run
the automatic `email-draft send` command for the same draft. The software blocks mixing
manual and automatic delivery modes.

## 7. Record the completed human action

> **Warning:** `mark-sent` does not send email. Run it only after the human has actually
> sent the message through the external mail client.

```powershell
uv run python -m app.cli.main agent email-draft mark-sent `
  --project-id 1 `
  --company-id 2 `
  --contact-id 3 `
  --email-draft-id 6 `
  --confirm `
  --output text
```

Duplicate confirmation and drafts with any automatic delivery attempt are rejected.

## 8. Verify the manual status

Use the read-only export command again:

```powershell
uv run python -m app.cli.main agent email-draft export `
  --project-id 1 `
  --company-id 2 `
  --contact-id 3 `
  --email-draft-id 6 `
  --output json
```

Verify `outreach_status` is `MANUALLY_SENT` and inspect `sent_at`, `recipient_email`, and
`manual_send_record_id`. This status means only that an operator recorded a manual send;
it does not claim SMTP acceptance, delivery, or inbox placement.
