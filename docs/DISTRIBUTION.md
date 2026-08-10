# Packaging and distribution

Penguin's release build is a macOS app bundle containing the local FastAPI source, `uv`,
`cloudflared`, and the loopback-only WhatsApp Go bridge. It does not contain a bearer token,
tunnel account credential, message database, contact data, pairing QR, or WhatsApp session.

## Build a release candidate

Install or provide current-architecture binaries, then build:

```bash
PENGUIN_CONNECT_WHATSAPP_BRIDGE_BIN=/absolute/path/to/whatsapp-bridge \
  ./scripts/build_desktop_app.sh --release
```

The output is `dist/Penguin.app`. The builder verifies that all three helper executables exist,
copies source without the developer virtualenv or tests, and ad-hoc signs by default. An ad-hoc
build is only for local verification.

For public distribution, set a Developer ID Application identity, sign, notarize with Apple's
notary service, staple the ticket, and test the exact downloaded artifact on a clean Mac:

```bash
PENGUIN_CONNECT_CODESIGN_IDENTITY="Developer ID Application: Example (TEAMID)" \
PENGUIN_CONNECT_WHATSAPP_BRIDGE_BIN=/absolute/path/to/whatsapp-bridge \
  ./scripts/build_desktop_app.sh --release
```

Set `PENGUIN_CONNECT_BUILD_ARCHS="arm64 x86_64"` only when all three supplied helpers are
universal. The builder rejects a helper that is missing a requested architecture. Do not
distribute a developer's WhatsApp store or `.env` alongside the app.

The `Release Penguin` GitHub workflow is the canonical public build. It pins and verifies the
SHA-256 digests for `uv` and `cloudflared`, checks out an exact commit from the maintained
WhatsApp bridge fork, cross-builds both architectures, and produces universal helpers and app
code. Public `v*` tags fail closed unless all Apple signing and notarization secrets exist:

- `APPLE_DEVELOPER_ID_P12_BASE64`
- `APPLE_DEVELOPER_ID_P12_PASSWORD`
- `APPLE_ID`
- `APPLE_TEAM_ID`
- `APPLE_APP_SPECIFIC_PASSWORD`

The workflow notarizes and staples both the app and DMG, runs Gatekeeper assessment, and then
publishes the DMG and ZIP to GitHub Releases. A manual workflow run may produce an unsigned
test artifact, but that artifact is not a consumer release.

## First run

On first launch, the app uses bundled `uv` to create a private runtime under
`~/Library/Application Support/PenguinConnect/runtime`. Installing Python dependencies requires
an Internet connection. The signed application itself remains read-only; runtime state,
WhatsApp session data, endpoint metadata, and logs live outside the app bundle.

The setup assistant guides the user through:

1. Grant Penguin Full Disk Access so it can read the local Apple Messages database.
2. Optionally grant Contacts access.
3. Scan an in-app, loopback-only WhatsApp QR from WhatsApp's Linked Devices screen. Pairing
   material is not returned by the status API, cached by the web view, or written to logs.
4. Select Read Only (the default) or Full Access.
5. Select a stable Tailscale Funnel or a temporary Cloudflare Quick Tunnel.
6. Paste the copied connection JSON into Slashy's MCP Settings.

The app installs separate launch agents for the WhatsApp bridge and authenticated MCP origin.
Cloudflare's temporary option has its own launch agent; stable Tailscale Funnel state is managed
by the signed-in Tailscale app. All origin services bind to loopback. Independent bearer and
daily-code secrets are generated on the Mac, stored in Keychain, and omitted from logs and
launchd plists. A requested connection bundle combines the bearer with today's six-character
code; the resulting wire credential expires at the next local midnight.

If a source-based installation already has a paired session under
`~/whatsapp-mcp/whatsapp-bridge/store`, the packaged installer migrates only its session and
message databases into Application Support when the destination is still unpaired. It leaves
the legacy store untouched and preserves the fresh unpaired destination as a timestamped backup.

## Endpoint durability

One-click setup defaults to Tailscale Funnel on dedicated HTTPS port `10000`. It produces a
predictable URL under the Mac's `ts.net` device name, uses valid TLS, and persists in Tailscale's
background configuration. The user must install and sign in to the free Tailscale Mac app.

Cloudflare Quick Tunnel is the no-account fallback, but its public hostname changes after the
tunnel or Mac restarts. A future first-party managed relay could remove the Tailscale
prerequisite, but must still provision one unique revocable credential per installation.

Other supported durable models are:

- a named Cloudflare Tunnel and hostname owned by the user;
- a Slashy control-plane endpoint that provisions one unique, revocable tunnel credential per
  installation; or
- another authenticated relay with per-install credentials and stable routing.

Never ship one shared Cloudflare tunnel credential inside the app. For a named tunnel, set
`PENGUIN_CONNECT_CLOUDFLARE_TUNNEL`, optional `PENGUIN_CONNECT_CLOUDFLARE_CONFIG`, and
`PENGUIN_CONNECT_PUBLIC_MCP_URL` while running remote setup.

## Disable and uninstall

The setup assistant's **Stop remote access** action stops and disables the public tunnel and
remote MCP launch agent. The app's **Rotate key** action immediately invalidates the old bearer
and copies a replacement bundle. **Copy Today’s Access Code** is also available from the Penguin
menu; copying the connection again always includes the current code.

For removal, run the packaged uninstaller before moving the app to Trash:

```bash
/Applications/Penguin.app/Contents/Resources/PenguinConnect/scripts/uninstall_penguin_connect.py
```

The default removes launch agents, endpoint metadata, and the Keychain bearer while preserving
local indexes and the WhatsApp session for a reinstall. Permanent deletion is deliberately
explicit:

```bash
/Applications/Penguin.app/Contents/Resources/PenguinConnect/scripts/uninstall_penguin_connect.py \
  --delete-data --yes
```

## Release verification

Before publishing, verify on a clean user account and both supported CPU architectures:

- Gatekeeper accepts the downloaded, notarized app;
- first-run runtime installation completes;
- the local UI and MCP health endpoints listen only on `127.0.0.1`;
- WhatsApp pairing survives an app and Mac restart;
- a missing bearer, missing daily code, wrong bearer, wrong daily code, or previous-day bundle receives HTTP 401;
- Read Only lists no write tools;
- every MCP action requires today's six-character code in addition to the install bearer;
- Full Access requires exact one-use confirmation for every write, without opening a Mac dialog;
- WhatsApp group creation accepts only exact unique phone numbers or user JIDs;
- iMessage group creation stages an addressed draft and does not silently send;
- unknown providers, local attachment paths, and local-only MCP tools cannot be retrieved;
- rotating the Keychain bearer revokes the old Slashy connection;
- Tailscale Funnel restart preserves the public URL;
- stopping remote access survives logout/login until setup explicitly re-enables it;
- the default uninstaller revokes both Keychain secrets without deleting local session data.
