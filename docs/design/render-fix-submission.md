# Design note — "Use my VI to fix lvkit" submission button

Status: **proposal** (not approved for implementation)
Scope: the render service (`deploy/cloudrun/`) + the pragmatest render demo (task #117)
Related: issues #2 (structural VI submission), #8 (semantic probe — the gold-standard path)

## 1. Purpose

The render service already receives a single `.vi` upload at `POST /render` and
**discards it immediately** after producing SVG. This note specs an **opt-in**
button that lets a user say *"this didn't render/convert correctly — keep my VI
and use it to fix lvkit,"* capturing a consented, attested bug sample at the
exact moment of failure.

This is the **structural path** front door (see the two-path submission model):
render/parse failures need the actual bytes. Behavioral ("wrong output")
questions should still use the **semantic path** — a probe + observed I/O, no
file (issue #8) — and are out of scope here.

## 2. Principles (non-negotiable)

1. **Default is still discard.** Retention happens *only* on explicit opt-in.
2. **Opt-in is a real affirmation.** Checkboxes unchecked by default; submit
   disabled until all are checked. No pre-checked / dark-pattern consent.
3. **Private, never public.** Retained VIs go to a locked-down bucket, readable
   only by the maintainer. Unlike a public issue zip, nothing is redistributed.
4. **Custodian discipline.** VIs may carry confidential logic/constants/secrets.
   Treat every retained file as sensitive: least-access, TTL, deletable.
5. **Clean-room on *use* is unchanged.** A retained VI is a black-box fixture /
   trigger. Semantics still come from public docs + observed behavior; NI
   `vi.lib` internals are never mined. Auto-flag NI content on ingest.

## 3. UX flow

```
[ user uploads VI ] → POST /render
        │
        ├─ 200 SVG rendered ─────────────► show SVG
        │                                    └─ secondary link: "Rendered wrong?"
        │
        └─ 422 could-not-render ──────────► show error
                                             └─ primary CTA: "Help fix this"
                                                        │
                                                        ▼
                                   ┌─────────────────────────────────────┐
                                   │  Submit-to-fix modal                 │
                                   │  (the SAME uploaded bytes, no re-up) │
                                   │  ☐ right-to-share                    │
                                   │  ☐ license-compliance (EULA)         │
                                   │  ☐ no NI-shipped content             │
                                   │  [optional] email  [optional] note   │
                                   │  [ Submit ] (disabled until 3 ✓)     │
                                   └─────────────────────────────────────┘
                                                        │
                                                        ▼
                                              POST /submit-fix
                                                        │
                                              "Thanks — ref #<id>"
```

- The modal reuses the **bytes already in the browser** from the render attempt;
  the user does not re-upload.
- Both success and failure surface the option (a render can look wrong without
  erroring), but failure makes it the primary CTA.

## 4. Attestation text (verbatim, version this string)

> By submitting this VI you affirm that:
> 1. **You have the right to share it.** It is your own work, or you are
>    otherwise permitted to disclose it.
> 2. **Sharing it complies with your software licenses,** including the LabVIEW
>    End-User License Agreement.
> 3. **It contains no NI-shipped or third-party proprietary content** you are not
>    entitled to share (e.g. `vi.lib` VIs, NI runtime files).
>
> You grant PragmaTest a license to use this VI solely to diagnose and improve
> lvkit. It is stored privately, is not published, and is deleted on request or
> after {RETENTION_DAYS} days. Email {CONTACT} to have it removed sooner.

Store the exact text under a version tag (e.g. `attestation_v1`) and record which
version the user agreed to.

## 5. Endpoint — `POST /submit-fix`

New Flask route alongside `render()` in `main.py`. Mirror the existing CORS +
size-cap handling.

**Request** (multipart/form-data):
| field | type | required | notes |
|-------|------|----------|-------|
| `vi` | file | yes | same bytes as the render attempt |
| `attest_right` | `"true"` | yes | reject unless present & true |
| `attest_license` | `"true"` | yes | " |
| `attest_no_ni` | `"true"` | yes | " |
| `attestation_version` | string | yes | e.g. `attestation_v1` |
| `render_error` | string | no | the 422 message that prompted this |
| `email` | string | no | for follow-up only; optional |
| `note` | string | no | free-text repro detail |

**Responses:**
- `200 application/json` `{"id": "<submission_id>", "status": "received"}`
- `400` missing VI or any attestation not true (reuse existing 400 style)
- `413` over the existing 25 MB cap
- `422` bytes are not a parseable VI (don't store garbage)

Server rejects if **any** of the three attestations is missing/false — the
attestation is a gate, not metadata.

## 6. Storage

- **Bucket:** `gs://lvkit-fix-submissions` — uniform bucket-level access,
  **no public access**, maintainer-only IAM. Separate from anything the render
  path touches (keep `/render` effectively stateless).
- **Object key:** `submissions/{YYYY}/{MM}/{submission_id}.vi`
  (`submission_id` = server-generated UUID; never trust client filename).
- **Sidecar metadata:** `submissions/{YYYY}/{MM}/{submission_id}.json`:
  ```json
  {
    "id": "…", "received_at": "<ISO8601>",
    "attestation_version": "attestation_v1",
    "attest_right": true, "attest_license": true, "attest_no_ni": true,
    "render_error": "…", "email": "…|null", "note": "…|null",
    "ni_content_detected": false, "ni_content_removed": false,
    "source_ip_hash": "<salted hash, not raw IP>",
    "vi_sha256": "…", "byte_size": 12345
  }
  ```
- **Lifecycle rule:** auto-delete objects after `RETENTION_DAYS` (propose **90**).
- **Deletion on request:** email `{CONTACT}`; delete by `id`. (No auth’d
  self-serve delete in v1 — keep it simple.)

## 7. Ingest processing

On receipt, before storing:
1. **Validate parseability** — if it isn't a VI, return 422, store nothing.
2. **NI-content scan** — inspect for `vi.lib` paths / NI-shipped VI signatures.
   Record `ni_content_detected`. For a **single `.vi`** the redistribution risk
   is low (a VI *references* vi.lib by path; it does not embed NI's source), so
   detection is informational. If we ever accept **zips/libraries**, strip
   vi.lib entries and set `ni_content_removed` = true. (v1 accepts single `.vi`
   only, matching `/render`.)
3. **Hash + size** for dedup and the metadata record.
4. **Store** VI + sidecar.

## 8. Notification / triage

Issue #2 showed the failure mode is *not hearing about submissions*. On store:
- Publish a lightweight notification (email via a simple send, or Pub/Sub → email)
  containing `id`, `render_error`, `note`, and a signed link to the object.
- Optionally append a line to a private triage log.

No public artifact is created — triage is entirely private.

## 9. Privacy / legal surface

- **Data controller:** Ryan Friedman (PragmaTest). A one-line privacy notice sits
  under the button and links to a short policy: what's stored, why, how long,
  how to delete.
- The attestation shifts submitter-side risk (right-to-share, EULA) to the
  submitter — good-faith cover + claim-back, **not** a waiver of NI/third-party
  rights. Private retention for interop debugging is far more defensible than
  public redistribution.
- Store the minimum: VI bytes + the fields above. Don't log raw IPs (hash them).

## 10. Non-goals (v1)

- No public corpus, no auto-publishing of submitted VIs.
- No zip/library uploads (single `.vi` only, as `/render` today).
- No authenticated user accounts / self-serve dashboard.
- No automatic use of a submission to change definitions — a human triages, and
  clean-room source-discipline still governs any resulting fix.

## 11. Decisions needed

1. `RETENTION_DAYS` value (proposed 90).
2. Notification channel (plain email vs Pub/Sub → email).
3. Contact address for deletion requests.
4. Where the privacy policy text lives (pragmatest page vs a repo doc).
5. Confirm v1 stays single-`.vi` (defer zip/library + vi.lib-strip to v2).
