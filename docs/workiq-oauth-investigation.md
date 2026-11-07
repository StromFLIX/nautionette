# WorkIQ CLI-owned OAuth bridge

## Decision and scope

**GO for CLI-owned loopback OAuth with WorkIQ 1.0.1's browser-auth setting.**
The bridge is implemented and tested through the real agentgateway, backend
APIs, production desktop receiver/controller, CLI browser hook and MCP protocol.
The actual WorkIQ CLI also accepted forwarded callbacks in a headless environment.

That is not a claim that a particular tenant has issued valid tokens or permits
unattended server use. A real account sign-in, a permitted WorkIQ operation, and
provider refresh/reauthentication behavior remain production acceptance tests.
Native-broker-only/device-bound tenant policies cannot be bypassed by this bridge.

## What the previous "Microsoft callback" error meant

WorkIQ's default Linux branch configures a native authentication broker and:

```
https://login.microsoftonline.com/common/oauth2/nativeclient
```

This is a configured native-client/embedded-browser redirect convention, **not
an observed callback being received at a Microsoft server**. Without a working
broker, the attempted system-browser path rejects that URI because it requires
an HTTP loopback callback. That happens before a usable localhost login starts.

WebKitGTK is a native runtime dependency, not an OAuth restriction. Loading it
removed the library error but did not fix the default broker/fallback mismatch.

## Supported switch verified in the shipped executable

The npm package `@microsoft/workiq@1.0.1` reports native CLI version
`1.0.1.47154`. Its shipped `workiq.dll` contains:

- `ConfigSetHandler.Execute`: accepts `disableBrokeredAuth`.
- `ClientAppFactory.CreatePublicClientApp`: checks that setting before the OS
  branches; sets `http://localhost` and builds the public client without broker.
- `LoginHandler`: prints explicit success/failure messages; a failed login may
  still exit 0. Nautionette checks those messages, not exit code alone.

```sh
# Same HOME/configuration directory as the gateway-hosted MCP process:
workiq config set disableBrokeredAuth=true
workiq auth login
```

This chooses normal browser OAuth, not unauthenticated access. Older issue
comments claiming there was no override do not describe the setting verified in
this version. The preset also selects English status messages for a deterministic
adapter, and accepts the CLI EULA only after the user's explicit checkbox.

## Actual WorkIQ runtime evidence

The executable ran with a fresh scratch HOME and no DISPLAY, DBus session,
WebKitGTK library path or native broker. It generated Microsoft authorization
URLs with PKCE `S256`, dynamic `http://localhost:<port>` redirects and
`response_mode=form_post`.

The implemented supervisor/browser hook captured actual CLI-selected ports
`44755` and `40125` on consecutive attempts. Its process/socket-ownership checks
passed, and both POST callbacks were forwarded to the real CLI listener:

1. A deliberately invalid synthetic code reached Microsoft and was rejected.
   The CLI requested a fresh browser login on a new port.
2. A controlled `access_denied` POST reached the CLI, which reported failure.
   The supervisor correctly marked the job failed despite the CLI's exit code 0.

No Microsoft account, valid token, EULA acceptance or workplace-data query was
used in this probe. Earlier GET-only callback testing was insufficient: MSAL
rejects GET for its form_post flow even if the callback page answers HTTP 200.

## Implemented architecture

```
Gateway CLI creates PKCE/state + loopback listener
  → browser hook captures its actual authorization URL
  → authenticated backend API returns the pending request to the desktop
  → desktop binds the exact local port before opening the system browser
  → provider posts or redirects to that desktop listener
  → desktop forwards fields/method through authenticated HTTPS backend APIs
  → private gateway control helper forwards once to the CLI-owned socket
  → CLI exchanges the code and stores/refreshes its own credentials
  → backend verifies MCP initialize/tools/list before saving the target
```

The backend-owned OAuth draft was replaced. Nautionette does not generate the
provider PKCE verifier, exchange the code or manage provider refresh tokens.

### Boundaries

- Only a public HTTPS authorization URL requesting code + PKCE S256, state,
  and a supported loopback query/form_post redirect is accepted.
- Linux process-group/socket ownership is checked at URL capture and immediately
  before callback forwarding. There is no arbitrary localhost HTTP proxy.
- The helper's port 4001 is authenticated and private to Docker, never published
  in the production Compose configuration. User-facing login requires APP_TOKEN.
- Browser hooks use a private temporary Unix socket, peer/process-group checks
  and an acknowledgement to avoid a fast hook-exit race.
- GET/query and POST/form_post methods/fields are preserved. Cookies, browser
  headers and CLI callback HTML are not relayed or echoed.
- Callback captures are one-shot, including uncertain deliveries. Retrying a
  lost result cannot repeat the network mutation; different fields are refused.
- CLI logins expire after ten minutes and their process groups are stopped on
  cancellation, failure, expiry or shutdown. Backend/gateway restart requires a
  fresh pending login; established CLI caches/target configuration persist.
- New OAuth targets use an isolated HOME under `/data/mcp/oauth/<name>` unless
  overridden. Existing stdio HOME/environment values are retained exactly.
- CLI argv/env execute literally in hybrid gateway resources. Real gateway tests
  caught and fixed dollar doubling so login and MCP processes agree, including
  intentional `$` and `$$` values. Existing stored values remain as actually run;
  operators can re-enter previously doubled values if they were unintended.
- CLI refresh is provider-owned. When interaction is required again, reconnect
  from the desktop; arbitrary prompts from normal MCP sessions are not relayed
  outside an explicit login job.
- Web/mobile/old desktop clients are told to switch/update. Unsupported native
  broker/device-code/implicit/non-interceptable flows fail rather than pretending
  every OAuth app is compatible.
- Account access is shared across all chats/users of the instance. Trusted CLI
  code shares the gateway's UID, network and credential-bearing volume; cleared
  inherited environment is not a sandbox. Removing a target does not revoke
  provider consent or remove the CLI account cache.

## Tests

Default tests cover real processes/sockets/hooks, private-control authentication,
port ownership, method/state validation, credential privacy, cancellation,
concurrent HOME protection, client compatibility, EULA gating, failed-save and
lost-result handling. Desktop tests cover query/form_post, port conflicts,
IPv6 localhost and unsupported clients.

Full synthetic E2E uses the real pinned gateway binary and production
receiver/controller. Two loopback interfaces emulate the desktop/gateway's
separate network namespaces. The synthetic authority ties its code to the CLI's
PKCE challenge; only the CLI sees the verifier. Its cache must match the MCP
process's environment before real MCP discovery can succeed.

```sh
NAUTIONETTE_TEST_GATEWAY_BINARY=/path/to/agentgateway \
  uv run pytest tests/backend/test_mcp_oauth_e2e.py tests/agentgateway/test_stdio_runtime.py
cd services/frontend
npm test
npx playwright test tests/mcp-oauth.spec.js tests/mcp-stdio.spec.js
```

CI downloads the checksum-pinned gateway release and runs the full E2E. Desktop
builds test loopback sockets on Windows/macOS/Linux; the packaged Linux smoke
additionally checks the actual isolated preload/IPC and form_post listener.

## Public corroboration

- [WorkIQ container authentication issue #67](https://github.com/microsoft/work-iq/issues/67):
  default broker/fallback mismatch; a project member notes that some tenants
  require brokered authentication.
- [Headless authentication issue #106](https://github.com/microsoft/work-iq/issues/106).
- [Hosted plugin configuration](https://github.com/microsoft/work-iq/blob/main/plugins/workiq/.mcp.json):
  a different hosted integration. Its port 12798 is not the CLI's dynamic port.

See [MCP server setup](mcp-servers.md#cli-owned-oauth-desktop-callback-bridge)
for deployment and the WorkIQ/generic desktop forms.
