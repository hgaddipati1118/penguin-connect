import Cocoa
import WebKit

private let defaultPort = 9000

private func htmlPage(title: String, detail: String, retry: Bool = false) -> String {
    let retryButton = retry
        ? "<button onclick=\"window.location.reload()\">Try again</button>"
        : ""
    return """
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          :root { color-scheme: light; }
          * { box-sizing: border-box; }
          body {
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            background: #f6f4ee;
            color: #20231f;
            font: 14px -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
          }
          main { width: min(440px, calc(100vw - 48px)); text-align: center; }
          .mark {
            width: 58px;
            height: 58px;
            margin: 0 auto 22px;
            display: grid;
            place-items: center;
            border-radius: 16px;
            background: #19231f;
            color: #f8f5ed;
            font: 700 30px Georgia, serif;
            box-shadow: 0 12px 30px rgba(29, 37, 33, .15);
          }
          h1 { margin: 0 0 8px; font: 600 27px Georgia, serif; }
          p { margin: 0 auto; max-width: 360px; color: #727972; line-height: 1.55; }
          .pulse {
            width: 5px;
            height: 5px;
            margin: 24px auto 0;
            border-radius: 999px;
            background: #2c9a6c;
            box-shadow: -12px 0 #9fcab8, 12px 0 #9fcab8;
            animation: breathe 1.1s ease-in-out infinite alternate;
          }
          button {
            margin-top: 22px;
            border: 1px solid #ccd0ca;
            border-radius: 10px;
            padding: 9px 15px;
            background: #fff;
            color: #242823;
            font: inherit;
            font-weight: 600;
          }
          @keyframes breathe { to { opacity: .35; transform: scale(.8); } }
        </style>
      </head>
      <body>
        <main>
          <div class="mark">P</div>
          <h1>\(title)</h1>
          <p>\(detail)</p>
          \(retry ? retryButton : "<div class=\"pulse\" aria-label=\"Loading\"></div>")
        </main>
      </body>
    </html>
    """
}

private func repoPort(at repoURL: URL) -> Int {
    if let value = ProcessInfo.processInfo.environment["PENGUIN_CONNECT_PORT"],
       let port = Int(value),
       (1...65535).contains(port) {
        return port
    }

    let envURL = repoURL.appendingPathComponent(".env")
    guard let contents = try? String(contentsOf: envURL, encoding: .utf8) else {
        return defaultPort
    }
    for rawLine in contents.split(whereSeparator: \.isNewline) {
        let line = rawLine.trimmingCharacters(in: .whitespaces)
        guard !line.hasPrefix("#"), line.hasPrefix("PENGUIN_CONNECT_PORT=") else { continue }
        let value = line.dropFirst("PENGUIN_CONNECT_PORT=".count)
            .trimmingCharacters(in: CharacterSet(charactersIn: "\"' "))
        if let port = Int(value), (1...65535).contains(port) {
            return port
        }
    }
    return defaultPort
}

private func makeDockIcon() -> NSImage {
    let image = NSImage(size: NSSize(width: 512, height: 512))
    image.lockFocus()
    NSColor(calibratedRed: 0.094, green: 0.133, blue: 0.116, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 28, y: 28, width: 456, height: 456), xRadius: 108, yRadius: 108).fill()
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = .center
    let attributes: [NSAttributedString.Key: Any] = [
        .font: NSFont(name: "Georgia-Bold", size: 285) ?? NSFont.boldSystemFont(ofSize: 285),
        .foregroundColor: NSColor(calibratedRed: 0.976, green: 0.961, blue: 0.918, alpha: 1),
        .paragraphStyle: paragraph,
    ]
    ("P" as NSString).draw(in: NSRect(x: 26, y: 87, width: 460, height: 330), withAttributes: attributes)
    image.unlockFocus()
    return image
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var bridgeProcess: Process?
    private var launchAttempted = false
    private var healthAttempts = 0

    private lazy var repoURL: URL = {
        let configured = Bundle.main.object(forInfoDictionaryKey: "PenguinRepoPath") as? String ?? ""
        return URL(fileURLWithPath: configured, isDirectory: true)
    }()

    private lazy var port = repoPort(at: repoURL)
    private var uiURL: URL {
        URL(string: "http://127.0.0.1:\(port)/penguin-connect/ui")!
    }
    private var healthURL: URL {
        URL(string: "http://127.0.0.1:\(port)/penguin-connect/health")!
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.applicationIconImage = makeDockIcon()
        configureMainMenu()
        configureWindow()
        showLoading()
        checkBridge()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    private func configureMainMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Penguin", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Penguin", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appMenuItem.submenu = appMenu

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu
        NSApp.mainMenu = mainMenu
    }

    private func configureWindow() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.allowsMagnification = true

        let rect = NSRect(x: 0, y: 0, width: 1280, height: 820)
        window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "Penguin"
        window.minSize = NSSize(width: 900, height: 620)
        window.center()
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
    }

    private func showLoading() {
        webView.loadHTMLString(
            htmlPage(
                title: "Opening Penguin",
                detail: "Starting your private local messaging workspace."
            ),
            baseURL: nil
        )
    }

    private func showError(_ detail: String) {
        webView.loadHTMLString(
            htmlPage(title: "Penguin could not start", detail: detail, retry: true),
            baseURL: nil
        )
    }

    private func checkBridge() {
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 1.2
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                if let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) {
                    self.webView.load(URLRequest(url: self.uiURL))
                    return
                }
                if !self.launchAttempted {
                    self.launchAttempted = true
                    self.launchBridge()
                }
                self.healthAttempts += 1
                if self.healthAttempts < 45 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                        self.checkBridge()
                    }
                } else {
                    self.showError(
                        "The local bridge did not become ready. Open Terminal with Full Disk Access, run scripts/run_penguin_connect_bridge.sh, then reopen Penguin."
                    )
                }
            }
        }.resume()
    }

    private func launchBridge() {
        let runner = repoURL.appendingPathComponent("scripts/run_penguin_connect_bridge.sh")
        guard FileManager.default.isExecutableFile(atPath: runner.path) else {
            showError("The Penguin bridge script is missing or is not executable.")
            return
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = [runner.path]
        process.currentDirectoryURL = repoURL
        let nullHandle = FileHandle.nullDevice
        process.standardOutput = nullHandle
        process.standardError = nullHandle
        do {
            try process.run()
            bridgeProcess = process
        } catch {
            showError("The local bridge could not be launched: \(error.localizedDescription)")
        }
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        let isLocal = url.host == "127.0.0.1" || url.host == "localhost" || url.scheme == "about"
        if navigationAction.navigationType == .linkActivated && !isLocal {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }
}

@main
struct PenguinApplication {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
    }
}
