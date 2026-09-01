Here's the key fact up front: **`wrangler` cannot natively upload a folder** — `wrangler r2 object put` accepts one object at a time and supports files up to 315 MB, so Cloudflare's official documentation points bulk/folder uploads to rclone or another S3-compatible tool. The guide below covers the documented wrangler path (scripted loop) plus the Cloudflare-recommended rclone path for true bulk uploads.[[developers.cloudflare](https://developers.cloudflare.com/r2/objects/upload-objects/)]

## Reality Check: wrangler vs. rclone

||wrangler `r2 object put`|rclone `copy`|
|---|---|---|
|Folder/bulk upload|No — one object per invocation [[developers.cloudflare](https://developers.cloudflare.com/r2/objects/upload-objects/)]|Yes — recursive, concurrent [[developers.cloudflare](https://developers.cloudflare.com/r2/examples/rclone/)]|
|Max file size|315 MB [[developers.cloudflare](https://developers.cloudflare.com/r2/objects/upload-objects/)]|5 TB (R2's max object size, via multipart) [[developers.cloudflare](https://developers.cloudflare.com/r2/examples/rclone/)]|
|Parallelism|None built-in|Built-in (`--transfers`) [[developers.cloudflare](https://developers.cloudflare.com/r2/examples/rclone/)]|
|Auth|Cloudflare account OAuth / API token|R2 API token (Access Key ID + Secret) [[developers.cloudflare](https://developers.cloudflare.com/r2/examples/rclone/)]|
|Best for|Small batches, CI one-offs|Large trees, big files, migrations|

Also worth knowing: a `wrangler r2 bulk put` command has been proposed and exists only in a limited form (a JSON manifest of objects via `--filename list.json`); a `--folder` flag was still an open feature request as of late 2025, so check `npx wrangler r2 bulk put --help` on your installed version before relying on it.[[github](https://github.com/cloudflare/workers-sdk/issues/11309)]

## Install and Authenticate on macOS Tahoe

Wrangler is officially supported on macOS 13.5+, so Tahoe (macOS 26) is fine, and it requires Node.js and npm — Cloudflare recommends installing Node via a version manager like mise or nvm to avoid permission issues.[[developers.cloudflare](https://developers.cloudflare.com/workers/wrangler/install-and-update/)]

Cloudflare recommends installing wrangler locally per project rather than globally:[[developers.cloudflare](https://developers.cloudflare.com/workers/wrangler/install-and-update/)]

```
# From any project directory (or a dedicated ~/r2-tools folder)
mkdir -p ~/r2-tools && cd ~/r2-tools
npm init -y
npm i -D wrangler@latest

# Verify
npx wrangler --version
```

Then authenticate. The simplest route is OAuth via browser:

```
npx wrangler login   # opens your browser to authorize
```

For headless/CI use, set the `CLOUDFLARE_API_TOKEN` environment variable, which takes top precedence in wrangler's auth chain (the legacy `CF_API_TOKEN` name is deprecated):[[developers.cloudflare](https://developers.cloudflare.com/workers/wrangler/commands/general/)][[community.cloudflare](https://community.cloudflare.com/t/problem-with-wrangler-authentication/745735)]

```
export CLOUDFLARE_API_TOKEN="<token-with-R2-edit-permission>"
```

If you need a bucket first: `npx wrangler r2 bucket create my-bucket`.[[developers.cloudflare](https://developers.cloudflare.com/r2/reference/wrangler-commands/)][[flaviocopes](https://flaviocopes.com/cloudflare-r2/)]

## Option A: wrangler Loop (Small Batches)

Since `r2 object put` takes exactly one destination path in the form `{bucket}/{key}` plus a `--file` flag, a bulk upload means iterating in the shell. Save this as `upload-folder.sh`:[[developers.cloudflare](https://developers.cloudflare.com/r2/reference/wrangler-commands/)]

```
#!/usr/bin/env zsh
set -euo pipefail

BUCKET="my-bucket"          # destination bucket
SRC="/path/to/folder"       # local folder to upload

cd "$SRC"
find . -type f | while read -r f; do
  key="${f#./}"             # preserve relative path as the object key
  echo "Uploading: $key"
  npx wrangler r2 object put "$BUCKET/$key" --file "$f" --remote
done
```

Two critical details:

- `--remote` is mandatory. Since Wrangler v4, `wrangler r2` commands target a local simulator of your bucket (the same one `wrangler dev` uses) and print only a warning unless you pass `--remote` — without it, "Upload complete" means the file landed in local simulation, not R2.[[github](https://github.com/cloudflare/workers-sdk/issues/9148)]
- Every file must be ≤ 315 MB. Filter with `find . -type f -size -300M` to be safe.[[developers.cloudflare](https://developers.cloudflare.com/r2/objects/upload-objects/)]

You can attach HTTP metadata per object with flags like `--content-type`, `--content-disposition`, and `--cache-control`. If your folder mixes types and you need explicit control, add a `case "$f" in (*.jpg) ct="image/jpeg" ;; ... esac` block and pass `--content-type "$ct"`.[[developers.cloudflare](https://developers.cloudflare.com/r2/reference/wrangler-commands/)]

## Option B: rclone (Cloudflare's Recommended Bulk Path)

For a genuinely large amount of files, Cloudflare's docs direct you to rclone over the S3 API. Install with `brew install rclone`, then create an R2 API token in the dashboard (R2 → Manage R2 API Tokens) to get an Access Key ID and Secret Access Key. Either run the interactive `rclone config` (choose _Amazon S3 Compliant_ → _Cloudflare R2_) or paste this directly into `~/.config/rclone/rclone.conf`:[[developers.cloudflare](https://developers.cloudflare.com/r2/objects/upload-objects/)][[developers.cloudflare](https://developers.cloudflare.com/r2/examples/rclone/)]

```
[r2]
type = s3
provider = Cloudflare
access_key_id = <ACCESS_KEY_ID>
secret_access_key = <SECRET_ACCESS_KEY>
endpoint = https://<ACCOUNT_ID>.r2.cloudflarestorage.com
```

Then upload and verify:

```
# Upload the whole folder, preserving structure, under an optional prefix
rclone copy /path/to/folder r2:my-bucket/optional-prefix --progress --transfers 16

# Verify what landed
rclone ls r2:my-bucket/optional-prefix
rclone check /path/to/folder r2:my-bucket/optional-prefix
```

One cost note from the R2 docs: every multipart part upload counts as a separate billable Class A operation, and a multipart upload always costs at least 3 operations (CreateMultipartUpload, UploadPart, CompleteMultipartUpload). Tune `--s3-chunk-size` and `--s3-upload-cutoff` — larger chunks mean fewer operations but costlier retries on failure.[[developers.cloudflare](https://developers.cloudflare.com/r2/examples/rclone/)]

## Gotchas and Verification

- No built-in listing: `wrangler r2 object` supports `get`, `put`, and `delete` but has no `list` subcommand (it's an open feature request), so verify uploads via `rclone ls`, the dashboard, or spot-check with `npx wrangler r2 object get my-bucket/some-key --remote`.[[github](https://github.com/cloudflare/workers-sdk/issues/13008)]
- Object keys, not folders: R2 has no real directories; keys containing `/` simulate folder structure, which is why preserving relative paths in both scripts above reproduces your tree.
- Metadata flags: `--content-type`, `--content-disposition`, `--cache-control` and related header flags are available on `r2 object put` if defaults don't suit your files.[[developers.cloudflare](https://developers.cloudflare.com/r2/reference/wrangler-commands/)]
- Updating wrangler later is just re-running `npm i -D wrangler@latest` in the project.[[developers.cloudflare](https://developers.cloudflare.com/workers/wrangler/install-and-update/)]

Bottom line: for a handful of small files, the wrangler loop in Option A is fine and keeps you in a single toolchain. For "a large amount of files," the documented, reliable answer on macOS Tahoe is rclone against R2's S3 endpoint — wrangler alone will be slow, serial, and capped at 315 MB per object.[[developers.cloudflare](https://developers.cloudflare.com/r2/objects/upload-objects/)][[developers.cloudflare](https://developers.cloudflare.com/r2/examples/rclone/)]


***

# Cloudflare R2 - Tuned Multipart Upload for Cost-effectiveness (2026)

Here's the tuned configuration, followed by the math that justifies each value. The short version: keep most files on single-part PUT (1 Class A op each), and for the files that must go multipart, use 64 MiB chunks instead of rclone's 5 MiB default.

## Recommended configuration (paste-ready)

```
rclone copy /path/to/folder r2:my-bucket/prefix \
  --s3-chunk-size 64Mi \
  --s3-upload-cutoff 256Mi \
  --s3-upload-concurrency 4 \
  --transfers 16 \
  --checkers 16 \
  --retries 3 \
  --low-level-retries 10 \
  --progress
```

Or bake it into `~/.config/rclone/rclone.conf` under your `[r2]` remote so every invocation inherits it:

```
[r2]
type = s3
provider = Cloudflare
access_key_id = <ACCESS_KEY_ID>
secret_access_key = <SECRET_ACCESS_KEY>
endpoint = https://<ACCOUNT_ID>.r2.cloudflarestorage.com
chunk_size = 64Mi
upload_cutoff = 256Mi
upload_concurrency = 4
```

## Why these numbers

The cost model: a single-part `PutObject` is exactly 1 Class A operation. A multipart upload costs `parts + 2` operations — CreateMultipartUpload, one UploadPart per chunk, and CompleteMultipartUpload — with every part billed separately . Class A operations run $4.50 per million (with 1 million free per month), while Class B reads cost $0.36 per million.[[cloudflare](https://www.cloudflare.com/products/r2/)][[developers.cloudflare](https://developers.cloudflare.com/r2/pricing/)]

rclone's defaults are hostile to that model: `--s3-chunk-size` defaults to 5 MiB and `--s3-upload-cutoff` to 200 MiB. Per 1,000 files of 1 GiB each:[[rclone](https://rclone.org/s3/)]

|Config|Ops per file|Total Class A ops|Cost @ $4.50/M|
|---|---|---|---|
|rclone defaults (5 MiB / 200 MiB)|205 parts + 2 = 207|207,000|$0.93|
|64 MiB chunk, 256 MiB cutoff|16 + 2 = 18|18,000|$0.081|
|1 Gi cutoff (single-part PUT)|1|1,000|$0.0045|
|500 × 10 GiB files, 64 MiB chunk|160 + 2 = 162|81,000|$0.36|
|500 × 10 GiB files, 256 MiB chunk|40 + 2 = 42|21,000|$0.095|

That's a 12× op reduction on large files just from the chunk size. The honest punchline: with 1M free Class A ops monthly, almost any one-time bulk upload costs pennies or nothing either way. Tuning matters for retry resilience, throughput, and recurring pipelines — not just the invoice.[[cloudflare](https://www.cloudflare.com/products/r2/)]

## Tuning decision rules

- **Set `--s3-upload-cutoff` just above your largest "normal" file.** Anything at or below the cutoff uploads as one PUT = 1 op. R2 allows single-part uploads up to 5 GiB and rclone's cutoff max is 5 GiB, so for media/backup datasets where files run 1–4 GiB, raising the cutoff to `1Gi`–`4Gi` collapses each file to a single op. The tradeoff is the quoted one: a failed single PUT retries the entire file, and single-part uploads get no intra-file parallelism.[[rclone](https://rclone.org/s3/)][[developers.cloudflare](https://developers.cloudflare.com/r2/platform/limits/)]
- **Set `--s3-chunk-size` between 32 MiB and 128 MiB.** Below 32 MiB you're paying ops for nothing; above 128 MiB each failed chunk retransmits more data (the "costlier retries" half of the tradeoff) and RAM buffers grow . 64 MiB is the sweet spot for typical broadband.
- **Don't fear the 10,000-part cap.** rclone automatically raises the chunk size for known-size files to stay under 10,000 parts, so 64 MiB handles files up to ~625 GiB without manual intervention; R2's absolute multipart ceiling is 4.995 TiB. R2 also requires equal-sized parts except the last, which rclone handles natively.[[developers.cloudflare](https://developers.cloudflare.com/r2/platform/limits/)][[forum.rclone](https://forum.rclone.org/t/upload-to-oracle-s3-fails-for-large-files/46046)][[github](https://github.com/apache/arrow/issues/41506)]
- **Watch RAM, not just ops.** Peak memory for multipart ≈ (concurrent big-file transfers) × `--s3-upload-concurrency` × `--s3-chunk-size`. With `--transfers 16`, concurrency 4, and 64 MiB chunks, worst case is a few GiB; halve concurrency if you're on a memory-tight machine.

## Profiles for different situations

|Profile|chunk-size|upload-cutoff|retries|When to use|
|---|---|---|---|---|
|Balanced (recommended)|64Mi|256Mi|3|Mixed trees: code, docs, photos|
|Archive / backup|128–256Mi|1Gi–4Gi|3|Multi-GB archives, reliable network|
|Flaky network|16–32Mi|128Mi|5, `--low-level-retries 20`|Hotel/cellular links where retry cost dominates|

## Verification and edge cases

- Verify with `rclone check /path/to/folder r2:my-bucket/prefix` — it uses HeadObject/GetObject calls, which are Class B at $0.36/million with 10M free monthly, so verification is effectively free compared to the upload itself.[[developers.cloudflare](https://developers.cloudflare.com/r2/pricing/)]
- Streaming edge case: files of unknown size (via `rclone rcat` or mounts) upload multipart at the configured chunk size, which at the 5 MiB default caps streamable size at ~48 GiB; the 64 MiB setting raises that ceiling automatically.[[rclone](https://rclone.org/s3/)]
- Keep `--s3-upload-concurrency 4` (the default): it parallelizes parts within one big file, which is why you don't want to push the cutoff to 5 GiB blindly — multipart is also your throughput lever for the few genuinely huge files.[[rclone](https://rclone.org/s3/)]