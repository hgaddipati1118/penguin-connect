# Packaging and distribution

Penguin's release build is a macOS app bundle containing the local FastAPI source, `uv`,
`cloudflared`, and the loopback-only WhatsApp Go bridge. It does not contain a bearer token,
Cloudflare account credential, message database, contact data, or WhatsApp session.

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

The bundled helpers in the current build are architecture-specific. Produce and merge both
arm64 and x86_64 helpers before calling a release universal. Do not distribute a developer's
WhatsApp store or `.env` alongside the app.

## First run

On first launch, the app uses bundled `uv` to create a private runtime under
`~/Library/Application Support/PenguinConnect/runtime`. Installing Python dependencies requires
an Internet connection. The signed application itself remains read-only; runtime state,
WhatsApp session data, endpoint metadata, and logs live outside the app bundle.

The user must:

1. Grant Penguin Full Disk Access so it can read the local Apple Messages database.
2. Choose **Pair WhatsApp…**, scan the QR code from WhatsApp's Linked Devices screen, wait for
   the connected message, and close the Terminal window.
3. Choose **Connect Slashy MCP…** and select Full Access or Read Only.
4. In Slashy MCP Settings, choose **Add MCP server** and paste the copied JSON into the custom
   server field.

The app installs separate launch agents for the WhatsApp bridge, authenticated MCP origin, and
Cloudflare tunnel. All origin services bind to loopback. The bearer is generated on the Mac,
stored in Keychain, omitted from logs and launchd plists, and placed on the clipboard only when
the user requests a connection bundle.

## Endpoint durability

One-click setup uses a Cloudflare Quick Tunnel. It is appropriate for onboarding and testing,
but its public hostname changes after the tunnel or Mac restarts. A durable consumer release
needs one of these explicit models:

- a named Cloudflare Tunnel and hostname owned by the user;
- a Slashy control-plane endpoint that provisions one unique, revocable tunnel credential per
  installation; or
- another authenticated relay with per-install credentials and stable routing.

Never ship one shared Cloudflare tunnel credential inside the app. For a named tunnel, set
`PENGUIN_CONNECT_CLOUDFLARE_TUNNEL`, optional `PENGUIN_CONNECT_CLOUDFLARE_CONFIG`, and
`PENGUIN_CONNECT_PUBLIC_MCP_URL` while running remote setup.

## Release verification

Before publishing, verify on a clean user account and both supported CPU architectures:

- Gatekeeper accepts the downloaded, notarized app;
- first-run runtime installation completes;
- the local UI and MCP health endpoints listen only on `127.0.0.1`;
- WhatsApp pairing survives an app and Mac restart;
- an unauthorized or wrong bearer receives HTTP 401;
- Read Only lists no write tools;
- Full Access requires exact confirmation and a local click for every write;
- unknown providers, local attachment paths, and local-only MCP tools cannot be retrieved;
- rotating the Keychain bearer revokes the old Slashy connection;
- named-tunnel restart preserves the public URL.
