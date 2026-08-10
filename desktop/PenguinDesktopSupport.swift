import Foundation

private func penguinHTMLEscape(_ value: String) -> String {
    value
        .replacingOccurrences(of: "&", with: "&amp;")
        .replacingOccurrences(of: "<", with: "&lt;")
        .replacingOccurrences(of: ">", with: "&gt;")
        .replacingOccurrences(of: "\"", with: "&quot;")
        .replacingOccurrences(of: "'", with: "&#39;")
}

func penguinHTMLPage(title: String, detail: String, retry: Bool = false) -> String {
    let action = retry
        ? """
          <a class="retry" href="penguin://retry">
            Try again
            <span aria-hidden="true">↗</span>
          </a>
          """
        : """
          <div class="pulse" role="status" aria-label="Starting Penguin">
            <span></span><span></span><span></span>
          </div>
          """
    return """
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          :root {
            color-scheme: light;
            --ink: #1d2925;
            --muted: #69746f;
            --paper: #fffdf7;
            --wash: #f3efe4;
            --coral: #e86f51;
            --mint: #a7cbb8;
          }
          * { box-sizing: border-box; }
          body {
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            overflow: hidden;
            background:
              radial-gradient(circle at 20% 16%, rgba(232, 111, 81, .12), transparent 28%),
              radial-gradient(circle at 82% 84%, rgba(111, 161, 137, .18), transparent 32%),
              var(--wash);
            color: var(--ink);
            font: 14px -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
          }
          body::before {
            content: "";
            position: fixed;
            inset: 0;
            opacity: .25;
            pointer-events: none;
            background-image: radial-gradient(rgba(29, 41, 37, .18) .55px, transparent .55px);
            background-size: 6px 6px;
          }
          main {
            position: relative;
            width: min(430px, calc(100vw - 48px));
            padding: 38px 40px 34px;
            text-align: center;
            border: 1px solid rgba(29, 41, 37, .1);
            border-radius: 28px;
            background: rgba(255, 253, 247, .88);
            box-shadow: 0 24px 70px rgba(29, 41, 37, .12);
            backdrop-filter: blur(18px);
          }
          .mark {
            width: 92px;
            height: 92px;
            margin: 0 auto 22px;
            object-fit: cover;
            border: 1px solid rgba(29, 41, 37, .1);
            border-radius: 27px;
            box-shadow: 0 14px 32px rgba(29, 41, 37, .13);
          }
          .eyebrow {
            margin-bottom: 9px;
            color: var(--coral);
            font-size: 10px;
            font-weight: 750;
            letter-spacing: .15em;
            text-transform: uppercase;
          }
          h1 {
            margin: 0 0 9px;
            font: 600 29px/1.12 Georgia, "Times New Roman", serif;
            letter-spacing: -.025em;
          }
          p {
            margin: 0 auto;
            max-width: 330px;
            color: var(--muted);
            line-height: 1.58;
          }
          .pulse {
            display: flex;
            justify-content: center;
            gap: 7px;
            margin-top: 25px;
          }
          .pulse span {
            width: 6px;
            height: 6px;
            border-radius: 99px;
            background: var(--mint);
            animation: breathe 1s ease-in-out infinite alternate;
          }
          .pulse span:nth-child(2) { animation-delay: .16s; }
          .pulse span:nth-child(3) { animation-delay: .32s; }
          .retry {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            margin-top: 24px;
            border: 1px solid var(--ink);
            border-radius: 999px;
            padding: 10px 16px;
            background: var(--ink);
            color: var(--paper);
            font-weight: 700;
            text-decoration: none;
            box-shadow: 0 8px 20px rgba(29, 41, 37, .16);
            transition: transform .16s ease, box-shadow .16s ease;
          }
          .retry:hover {
            transform: translateY(-1px);
            box-shadow: 0 11px 24px rgba(29, 41, 37, .2);
          }
          .retry:focus-visible {
            outline: 3px solid rgba(232, 111, 81, .36);
            outline-offset: 3px;
          }
          @keyframes breathe {
            from { opacity: .35; transform: translateY(2px) scale(.82); }
            to { opacity: 1; transform: translateY(-2px) scale(1); }
          }
          @media (prefers-reduced-motion: reduce) {
            .pulse span { animation: none; }
            .retry { transition: none; }
          }
        </style>
      </head>
      <body>
        <main>
          <img class="mark" src="PenguinIcon.png" alt="">
          <div class="eyebrow">Private by design</div>
          <h1>\(penguinHTMLEscape(title))</h1>
          <p>\(penguinHTMLEscape(detail))</p>
          \(action)
        </main>
      </body>
    </html>
    """
}

func isPenguinRetryURL(_ url: URL) -> Bool {
    url.scheme?.lowercased() == "penguin" && url.host?.lowercased() == "retry"
}

func isAllowedPenguinNavigationURL(_ url: URL, resourceURL: URL?) -> Bool {
    let scheme = url.scheme?.lowercased()
    if scheme == "about" {
        return true
    }
    if scheme == "http", url.host == "127.0.0.1" || url.host?.lowercased() == "localhost" {
        return true
    }
    guard url.isFileURL, let resourceURL else {
        return false
    }
    let resources = resourceURL.resolvingSymlinksInPath().standardizedFileURL.path
    let candidate = url.resolvingSymlinksInPath().standardizedFileURL.path
    return candidate == resources || candidate.hasPrefix(resources + "/")
}
