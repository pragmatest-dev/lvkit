# Signing the VS Code extension bundle (Smart App Control)

> **Status (2026-08-04): UNBLOCKED — Azure Artifact Signing is set up** (account
> `pragmatest`, certificate profile `pragmatest-public-trust`; the identity-
> validation issue below no longer blocks). Consequently the extension has
> **reverted from the bundled-uv/managed-Python approach (0.1.8/0.1.9) back to a
> signed standalone PyInstaller binary** — see `../../../editors/vscode/build/build-binary.sh`
> and `.github/workflows/publish-extension.yml` (win32-x64 builds on
> `windows-latest` and signs every PE with `azure/artifact-signing-action`).
> Part B below is implemented; this doc is kept for the history/rationale.
>
> One correction from when this was written: the action's GitHub repo/inputs
> changed — Microsoft renamed "Trusted Signing" to "Artifact Signing", so the
> action is now **`azure/artifact-signing-action@v2`** (the old
> `azure/trusted-signing-action` name redirects) with input
> **`signing-account-name`** (an alias `trusted-signing-account-name` still
> exists) instead of only `trusted-signing-account-name`. Verified against the
> action's `action.yml`/README at the pinned `v2` tag.

### Original blocker (since resolved)

The Azure Trusted Signing **Individual** path (Verified-ID flow) couldn't validate
a US address — the Verified ID emitted `WA`/`USA`/`XXXXX0000` (no hyphen) while the
billing/request forms forced `wa`/`US`/`XXXXX-0000` (hyphen), and none were
editable, so the literal match could never pass (filed, unanswered, MS Q&A
#5966637). By the time this revert was implemented, a working `pragmatest` /
`pragmatest-public-trust` Artifact Signing account + certificate profile existed
and the `AZURE_TENANT_ID`/`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET` secrets were in
place — exactly how the original blocker was cleared wasn't re-verified here (out
of scope for the code change); see Azure Portal / whoever set up the account for
specifics if it matters later.

## Why

The extension ships an **unsigned** bundled Python runtime (`python-build-standalone`
+ lvkit + deps). Windows **Smart App Control** (SAC), when in *enforce* mode
(default-on-and-committing on Windows 11), blocks unsigned, non-reputable native
code — so the bundled `python.exe` / `*.dll` / `*.pyd` (e.g. `select.pyd`) are
blocked *before lvkit runs*, and `render` dies with:

    ImportError: DLL load failed while importing select:
    An Application Control policy has blocked this file.

This is version-independent (0.1.10 and 0.1.11 fail identically once SAC enforces
— confirmed: `VerifiedAndReputablePolicyState = 0x1`, bundled `select.pyd`
`NotSigned`). The fix is to **code-sign every PE file in the bundle**.

## Two facts that shape it

1. **Tool: Azure Trusted Signing (a.k.a. Artifact Signing).** ~$10/mo, no
   hardware token (unlike OV/EV certs since 2023), first-class GitHub Actions
   support, Microsoft-managed CA whose **Public Trust** profile SAC honors — and
   SAC allows a *valid* Public-Trust signature even before cloud reputation
   builds. Identity: **Individual** path (US/Canada only) — us. Migratable to an
   **Organization** profile later if `pragmatest` incorporates (add a new
   identity validation + profile, swap `certificate-profile-name` in CI; per-
   identity reputation restarts but SAC still honors the valid signature).
2. **Scope: sign EVERY PE, not just the `.vsix`.** SAC evaluates each extracted-
   on-disk file by its own hash, so every `.exe` / `.dll` / `.pyd` under the
   bundled runtime must be signed. No PyInstaller-onefile shortcut (extracted
   DLLs are still evaluated).

## Part A — one-time Azure setup (manual, you)

1. Azure subscription with a **US** billing account whose legal name/address
   match the identity to validate.
2. Register the `Microsoft.CodeSigning` resource provider.
3. Create a **Trusted Signing account** (pick a region, e.g. `westus2`; note its
   signing endpoint, e.g. `https://wus2.codesigning.azure.net/`).
4. Create a **Certificate Profile**: type **Public Trust**, identity
   **Individual**. Complete identity validation (auto-sourced from billing; may
   require ID verification; can take a bit).
5. Create an **Entra app registration (service principal)**; grant it the
   **Trusted Signing Certificate Profile Signer** role on the account.
6. Add three **GitHub Actions secrets**: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
   `AZURE_CLIENT_SECRET`. (Record the account name, profile name, endpoint.)

## Part B — CI change (IMPLEMENTED)

`.github/workflows/publish-extension.yml` now has a **per-platform matrix**
(`os` field), and **only the `win32-x64` job** (`runs-on: windows-latest` — PE
Authenticode signing cannot run on Linux/macOS) signs, **after
`build-binary.sh` produces `editors/vscode/bin/lvkit/`** and **before
`vsce package`**:

```yaml
      - name: Code-sign the bundled binary (Artifact Signing)
        uses: azure/artifact-signing-action@v2
        with:
          azure-tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          azure-client-id: ${{ secrets.AZURE_CLIENT_ID }}
          azure-client-secret: ${{ secrets.AZURE_CLIENT_SECRET }}
          exclude-environment-credential: false
          exclude-workload-identity-credential: true
          exclude-managed-identity-credential: true
          exclude-shared-token-cache-credential: true
          exclude-visual-studio-credential: true
          exclude-visual-studio-code-credential: true
          endpoint: https://wus2.codesigning.azure.net/
          signing-account-name: pragmatest
          certificate-profile-name: pragmatest-public-trust
          files-folder: ${{ github.workspace }}\editors\vscode\bin\lvkit
          files-folder-filter: exe,dll,pyd
          files-folder-recurse: true
```

The action's repo was renamed `azure/trusted-signing-action` →
`azure/artifact-signing-action` (old name still redirects) and its input was
renamed `trusted-signing-account-name` → `signing-account-name` (the old name is
kept as an alias) — verified against the `v2` tag's `action.yml`/README, not
assumed. Artifact Signing adds RFC-3161 timestamping automatically (default
`timestamp-rfc3161: http://timestamp.acs.microsoft.com`). Linux/darwin jobs run
on their native runners and ship **unsigned** — mac notarization is a separate,
later item.

## Part C — verify (the gate)

1. Trigger the **dry-run** (`workflow_dispatch`) on the branch → download the
   signed `win32` `.vsix`.
2. Install on a **SAC-enforce** machine (the one that currently fails) → `render`
   a real VI. It should now run (valid signature → SAC allows).
3. Only then tag `ext-v0.1.11` to publish.

## Notes

- Signing dozens of `.pyd`/`.dll` adds CI time (each is a service round-trip);
  acceptable, and only on the release/dry-run path.
- Endpoint region must match the Trusted Signing account region.
- If SmartScreen shows a brief "less common" ramp on first releases, that's
  reputation building on a valid signature — not a block.
