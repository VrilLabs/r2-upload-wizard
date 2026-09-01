# Security Policy

## What this tool can do with your credentials

`r2-wizard` uses the R2 API token you provide (`CLOUDFLARE_ACCESS_KEY_ID` /
`CLOUDFLARE_SECRET_ACCESS_KEY`) to call R2's S3-compatible API directly:
list, create, and delete buckets, and upload/HEAD objects, scoped to
whatever permissions that token was issued with. `CLOUDFLARE_API_TOKEN` is
detected and validated for shape but is **not** called against any API in
this version.

## Credential handling

- Credentials are read from your shell environment first, then from a
  `.env` file in the current directory. A value already exported in your
  shell always wins over `.env`.
- The setup screen never displays a full secret value -- only a masked
  preview (all but the last 4 characters starred out).
- Values you type into the setup screen are written to `.env` in your
  current directory, never elsewhere, and never logged.
- **`.env` is not committed** -- it's excluded via `.gitignore`. Treat it
  like any other secret file: don't paste it into chat, issues, or CI logs.
- Set restrictive permissions on your `.env` file if you're on a shared
  machine, e.g. `chmod 600 .env`.

## Scope of destructive actions

- Bucket **delete** requires typing the bucket's name to confirm and is
  refused outright if the bucket is not empty -- this tool never empties a
  bucket on your behalf.
- Upload **overwrite** only happens when you explicitly choose
  "Overwrite all" on the confirmation screen for a given run; the default
  is to skip files that already exist at the destination with a matching
  size.

## Reporting a vulnerability

If you find a security issue in this project, please open a private
security advisory on the repository (GitHub's "Report a vulnerability"
under the Security tab) rather than a public issue, so it can be addressed
before details are public. Include reproduction steps and the affected
version.
