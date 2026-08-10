import Foundation

func penguinOnboardingHTMLPage(initialStep: Int = 0) -> String {
    let safeInitialStep = max(0, min(initialStep, 4))
    return """
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data: http://127.0.0.1:8081; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src http://127.0.0.1:8081">
        <style>
          :root {
            color-scheme: light;
            --ink: #17241f;
            --muted: #66716c;
            --paper: #fffdf7;
            --wash: #eeeadd;
            --line: rgba(23, 36, 31, .12);
            --coral: #df684b;
            --green: #285f4c;
            --green-soft: #e5f0e9;
          }
          * { box-sizing: border-box; }
          [hidden] { display: none !important; }
          body {
            margin: 0;
            min-height: 100vh;
            overflow: hidden;
            color: var(--ink);
            background:
              radial-gradient(circle at 9% 8%, rgba(223, 104, 75, .13), transparent 27%),
              radial-gradient(circle at 88% 91%, rgba(40, 95, 76, .16), transparent 31%),
              var(--wash);
            font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
          }
          button, input { font: inherit; }
          .shell {
            min-height: 100vh;
            display: grid;
            grid-template-columns: 224px minmax(0, 1fr);
            padding: 22px;
            gap: 18px;
          }
          aside, main {
            border: 1px solid var(--line);
            background: rgba(255, 253, 247, .9);
            backdrop-filter: blur(20px);
            box-shadow: 0 20px 56px rgba(23, 36, 31, .09);
          }
          aside { border-radius: 24px; padding: 22px 17px; }
          main { border-radius: 28px; display: grid; place-items: center; padding: 34px; }
          .brand { display: flex; align-items: center; gap: 11px; margin: 0 5px 28px; }
          .brand img { width: 42px; height: 42px; border-radius: 13px; box-shadow: 0 8px 20px rgba(23, 36, 31, .14); }
          .brand strong { font: 600 20px Georgia, serif; letter-spacing: -.02em; }
          .steps { display: grid; gap: 5px; }
          .step-link {
            width: 100%; display: flex; align-items: center; gap: 10px; padding: 10px;
            border: 0; border-radius: 11px; color: var(--muted); background: transparent;
            text-align: left; cursor: default;
          }
          .step-link.active { color: var(--ink); background: rgba(23, 36, 31, .065); }
          .step-link.done .number { color: white; background: var(--green); border-color: var(--green); }
          .number {
            width: 24px; height: 24px; display: grid; place-items: center; flex: none;
            border: 1px solid var(--line); border-radius: 99px; font-size: 11px; font-weight: 750;
          }
          .privacy { position: absolute; left: 44px; bottom: 40px; width: 170px; color: var(--muted); font-size: 11px; line-height: 1.45; }
          .panel { display: none; width: min(620px, 100%); }
          .panel.active { display: block; animation: arrive .28s ease-out; }
          @keyframes arrive { from { opacity: 0; transform: translateY(7px); } }
          .eyebrow { color: var(--coral); font-size: 10px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
          h1 { margin: 9px 0 11px; font: 600 36px/1.08 Georgia, serif; letter-spacing: -.035em; }
          .lede { margin: 0 0 25px; max-width: 540px; color: var(--muted); font-size: 15px; }
          .card { border: 1px solid var(--line); border-radius: 18px; padding: 17px; background: rgba(255,255,255,.48); }
          .card + .card { margin-top: 10px; }
          .row { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
          .row strong { display: block; margin-bottom: 2px; }
          .row small { color: var(--muted); }
          .status { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); font-size: 12px; }
          .dot { width: 8px; height: 8px; border-radius: 99px; background: #b2b7b4; }
          .status.ok { color: var(--green); }
          .status.ok .dot { background: #3c8b69; }
          .actions { display: flex; align-items: center; gap: 9px; margin-top: 24px; }
          .button {
            border: 1px solid var(--ink); border-radius: 999px; padding: 10px 17px;
            background: var(--ink); color: var(--paper); font-weight: 720; cursor: pointer;
          }
          .button:hover { transform: translateY(-1px); }
          .button.secondary { border-color: var(--line); background: transparent; color: var(--ink); }
          .button:disabled { opacity: .45; cursor: default; transform: none; }
          .qr-wrap { display: grid; grid-template-columns: 220px 1fr; gap: 24px; align-items: center; }
          .qr-box { width: 220px; height: 220px; display: grid; place-items: center; border: 1px solid var(--line); border-radius: 22px; background: white; }
          .qr-box img { width: 190px; height: 190px; image-rendering: pixelated; }
          .qr-placeholder { color: var(--muted); font-size: 12px; text-align: center; padding: 28px; }
          .choices { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
          .choice { position: relative; border: 1px solid var(--line); border-radius: 18px; padding: 18px; cursor: pointer; background: rgba(255,255,255,.44); }
          .choice:has(input:checked) { border-color: var(--green); box-shadow: 0 0 0 2px rgba(40,95,76,.11); background: var(--green-soft); }
          .choice input { position: absolute; right: 16px; top: 16px; accent-color: var(--green); }
          .choice strong { display: block; margin-bottom: 7px; }
          .choice span { color: var(--muted); font-size: 12px; }
          .endpoint { margin-top: 13px; padding: 12px 14px; border-radius: 12px; background: rgba(23,36,31,.06); color: var(--muted); font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }
          .access-code { margin-top: 14px; display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 15px 17px; border: 1px solid rgba(40,95,76,.25); border-radius: 16px; background: var(--green-soft); }
          .access-code small { display: block; color: var(--muted); }
          .access-code output { color: var(--green); font: 750 24px ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .14em; }
          .notice { margin-top: 13px; padding: 12px 14px; border-radius: 12px; background: #fff3d8; color: #775b22; font-size: 12px; }
          .spinner { width: 14px; height: 14px; border: 2px solid rgba(23,36,31,.16); border-top-color: var(--green); border-radius: 50%; animation: spin .8s linear infinite; }
          @keyframes spin { to { transform: rotate(360deg); } }
          @media (prefers-reduced-motion: reduce) { .panel.active, .spinner { animation: none; } }
        </style>
      </head>
      <body>
        <div class="shell">
          <aside>
            <div class="brand"><img src="PenguinIcon.png" alt=""><strong>Penguin</strong></div>
            <div class="steps">
              <div class="step-link" data-nav="0"><span class="number">1</span>Welcome</div>
              <div class="step-link" data-nav="1"><span class="number">2</span>Mac access</div>
              <div class="step-link" data-nav="2"><span class="number">3</span>WhatsApp</div>
              <div class="step-link" data-nav="3"><span class="number">4</span>Remote access</div>
              <div class="step-link" data-nav="4"><span class="number">5</span>Connect Slashy</div>
            </div>
            <div class="privacy">Your message databases stay on this Mac. Only requested MCP results cross the encrypted connection.</div>
          </aside>
          <main>
            <section class="panel" data-step="0">
              <div class="eyebrow">Private Mac companion</div>
              <h1>Your messages, under your control.</h1>
              <p class="lede">Penguin connects iMessage, WhatsApp, and Contacts to tools you approve. Local services remain on loopback, secrets stay in Keychain, and every remote request needs today's access code.</p>
              <div class="card"><div class="row"><div><strong>Nothing is uploaded in bulk</strong><small>Clients receive only individual tool results they request.</small></div><span class="status ok"><span class="dot"></span>Local first</span></div></div>
              <div class="actions"><button class="button" onclick="go(1)">Set up Penguin</button></div>
            </section>
            <section class="panel" data-step="1">
              <div class="eyebrow">Step 2</div><h1>Let Penguin read your Mac.</h1>
              <p class="lede">macOS protects Messages and Contacts separately. Penguin cannot work around these controls.</p>
              <div class="card"><div class="row"><div><strong>Full Disk Access</strong><small>Needed only for your local Messages database.</small></div><span id="disk-status" class="status"><span class="dot"></span>Not checked</span></div></div>
              <div class="card"><div class="row"><div><strong>Contacts</strong><small>Used to match handles and save approved changes.</small></div><span id="contacts-status" class="status"><span class="dot"></span>Not checked</span></div></div>
              <div class="actions"><button class="button" onclick="post('openFullDiskAccess')">Open System Settings</button><button class="button secondary" onclick="post('checkPermissions')">Check again</button><button id="contacts-button" class="button secondary" onclick="post('requestContacts')">Allow Contacts</button></div>
              <div class="actions"><button id="permissions-next" class="button" disabled onclick="go(2)">Continue</button></div>
            </section>
            <section class="panel" data-step="2">
              <div class="eyebrow">Step 3</div><h1>Link WhatsApp.</h1>
              <p class="lede">Open WhatsApp on your phone, choose Linked Devices, then scan this one-time QR. The session is stored only in Penguin's Application Support folder.</p>
              <div class="qr-wrap"><div class="qr-box"><div id="qr-placeholder" class="qr-placeholder">Choose “Start pairing” to create a private QR.</div><img id="qr" alt="WhatsApp pairing QR" hidden></div><div><strong id="wa-title">WhatsApp is not linked</strong><p id="wa-detail" class="lede">The pairing server listens only on 127.0.0.1.</p><span id="wa-status" class="status"><span class="dot"></span>Not started</span></div></div>
              <div class="actions"><button id="wa-start" class="button" onclick="post('startWhatsApp')">Start pairing</button><button class="button secondary" onclick="go(3)">Skip for now</button><button id="wa-next" class="button" disabled onclick="go(3)">Continue</button></div>
            </section>
            <section class="panel" data-step="3">
              <div class="eyebrow">Step 4</div><h1>Choose what Slashy may do.</h1>
              <p class="lede">Every request uses a six-character code that changes each day. Writes also require an exact one-use confirmation bound to the requested action.</p>
              <div class="choices">
                <label class="choice"><input type="radio" name="profile" value="read-only" checked><strong>Read only</strong><span>Search and read messages and Contacts. No send or contact changes.</span></label>
                <label class="choice"><input type="radio" name="profile" value="slashy"><strong>Full access</strong><span>Add sending, contact changes, and group creation. Every request requires today's code.</span></label>
              </div>
              <p class="lede" style="margin: 22px 0 10px">Choose the encrypted public connection.</p>
              <div class="choices">
                <label class="choice"><input type="radio" name="tunnel" value="tailscale" checked><strong>Stable Tailscale URL</strong><span>Recommended. Keeps the same ts.net address across app and Mac restarts. Requires the free Tailscale Mac app.</span></label>
                <label class="choice"><input type="radio" name="tunnel" value="cloudflare-quick"><strong>Temporary Cloudflare URL</strong><span>No account required, but the address changes whenever the tunnel restarts.</span></label>
              </div>
              <div id="setup-progress" class="actions" hidden><span class="spinner"></span><span>Installing private background services…</span></div>
              <div class="actions"><button id="setup-button" class="button" onclick="setupRemote()">Create secure endpoint</button><button class="button secondary" onclick="post('openTailscale')">Get Tailscale</button><button class="button secondary" onclick="post('stopRemote')">Stop remote access</button></div>
            </section>
            <section class="panel" data-step="4">
              <div class="eyebrow">Ready</div><h1>Connect Penguin to Slashy.</h1>
              <p id="ready-detail" class="lede">Your connection bundle is copied. It contains today's access credential and expires when the six-character code rotates at the start of the next local day.</p>
              <div id="endpoint" class="endpoint">Waiting for endpoint…</div>
              <div class="access-code"><div><strong>Today's access code</strong><small>Required for every MCP action</small></div><output id="daily-code" aria-live="polite">••••••</output></div>
              <div class="actions"><button class="button" onclick="post('copyConnection')">Copy today's connection</button><button class="button secondary" onclick="post('copyDailyCode')">Copy code</button><button class="button secondary" onclick="post('openSlashy')">Open Slashy</button><button class="button secondary" onclick="post('rotateToken')">Rotate key</button></div>
              <div class="actions"><button class="button" onclick="post('finish')">Open Penguin</button></div>
            </section>
          </main>
        </div>
        <script>
          const native = window.webkit?.messageHandlers?.penguin;
          let step = \(safeInitialStep);
          let diskGranted = false;
          let contactsGranted = false;
          function post(action, data = {}) { native?.postMessage({ action, ...data }); }
          function go(next) {
            step = next;
            document.querySelectorAll('.panel').forEach((node) => node.classList.toggle('active', Number(node.dataset.step) === step));
            document.querySelectorAll('.step-link').forEach((node) => {
              const index = Number(node.dataset.nav);
              node.classList.toggle('active', index === step);
              node.classList.toggle('done', index < step);
            });
            if (step === 1) post('checkPermissions');
            if (step === 2) post('checkWhatsApp');
            if (step === 3) post('remoteStatus');
            if (step === 4) post('dailyCode');
          }
          function setupRemote() {
            const profile = document.querySelector('input[name=profile]:checked')?.value || 'read-only';
            const tunnel = document.querySelector('input[name=tunnel]:checked')?.value || 'tailscale';
            document.getElementById('setup-button').disabled = true;
            document.getElementById('setup-progress').hidden = false;
            post('setupRemote', { profile, tunnel });
          }
          window.penguinNative = {
            permissions(disk, contacts, contactsState) {
              diskGranted = disk; contactsGranted = contacts;
              const diskNode = document.getElementById('disk-status');
              diskNode.classList.toggle('ok', disk); diskNode.lastChild.textContent = disk ? ' Allowed' : ' Not allowed';
              const contactsNode = document.getElementById('contacts-status');
              contactsNode.classList.toggle('ok', contacts); contactsNode.lastChild.textContent = contacts ? ' Allowed' : ' Not allowed';
              document.getElementById('contacts-button').textContent = contactsState === 'denied' || contactsState === 'restricted' ? 'Open Contacts Settings' : contacts ? 'Contacts allowed' : 'Allow Contacts';
              document.getElementById('permissions-next').disabled = !disk;
            },
            whatsapp(status, qrVersion) {
              const connected = status === 'connected';
              const waiting = status === 'waiting_for_scan';
              document.getElementById('wa-status').classList.toggle('ok', connected);
              document.getElementById('wa-status').lastChild.textContent = connected ? ' Linked' : waiting ? ' Waiting for scan' : ' ' + status.replaceAll('_', ' ');
              document.getElementById('wa-title').textContent = connected ? 'WhatsApp is linked' : waiting ? 'Scan with your phone' : 'Preparing WhatsApp';
              document.getElementById('wa-next').disabled = !connected;
              document.getElementById('wa-start').disabled = waiting || connected;
              const image = document.getElementById('qr');
              image.hidden = !waiting;
              document.getElementById('qr-placeholder').hidden = waiting;
              if (waiting) image.src = 'http://127.0.0.1:8081/pairing/qr.png?v=' + encodeURIComponent(qrVersion);
            },
            remoteResult(ok, endpoint, message) {
              document.getElementById('setup-button').disabled = false;
              document.getElementById('setup-progress').hidden = true;
              if (ok) { document.getElementById('endpoint').textContent = endpoint; go(4); }
              else { alert(message || 'Remote setup failed.'); }
            },
            remoteStatus(active, endpoint) {
              if (active && endpoint) document.getElementById('endpoint').textContent = endpoint;
            },
            dailyCode(code) {
              document.getElementById('daily-code').textContent = code || '••••••';
            }
          };
          go(step);
        </script>
      </body>
    </html>
    """
}
