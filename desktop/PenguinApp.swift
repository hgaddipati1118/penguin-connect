import Cocoa
import WebKit

private let defaultPort = 9000

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
    if let iconURL = Bundle.main.url(forResource: "PenguinIcon", withExtension: "png"),
       let icon = NSImage(contentsOf: iconURL) {
        return icon
    }

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
    private var healthGeneration = 0

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
        beginBridgeCheck()
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
            penguinHTMLPage(
                title: "Opening Penguin",
                detail: "Starting your private local messaging workspace."
            ),
            baseURL: Bundle.main.resourceURL
        )
    }

    private func showError(_ detail: String) {
        webView.loadHTMLString(
            penguinHTMLPage(title: "Penguin could not start", detail: detail, retry: true),
            baseURL: Bundle.main.resourceURL
        )
    }

    private func beginBridgeCheck() {
        healthGeneration += 1
        launchAttempted = false
        healthAttempts = 0
        showLoading()
        checkBridge(generation: healthGeneration)
    }

    private func checkBridge(generation: Int) {
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 1.2
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self, generation == self.healthGeneration else { return }
                if let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) {
                    self.webView.load(URLRequest(url: self.uiURL))
                    return
                }
                if !self.launchAttempted {
                    self.launchAttempted = true
                    guard self.launchBridge() else { return }
                }
                self.healthAttempts += 1
                if self.healthAttempts < 45 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                        self.checkBridge(generation: generation)
                    }
                } else {
                    self.showError(
                        "The local bridge did not become ready. Grant Penguin Full Disk Access, or run scripts/run_penguin_connect_bridge.sh from a Terminal that has Full Disk Access, then try again."
                    )
                }
            }
        }.resume()
    }

    private func launchBridge() -> Bool {
        if bridgeProcess?.isRunning == true {
            return true
        }
        let runner = repoURL.appendingPathComponent("scripts/run_penguin_connect_bridge.sh")
        guard FileManager.default.isExecutableFile(atPath: runner.path) else {
            showError("The Penguin bridge script is missing or is not executable.")
            return false
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [runner.path]
        process.currentDirectoryURL = repoURL
        let nullHandle = FileHandle.nullDevice
        process.standardOutput = nullHandle
        process.standardError = nullHandle
        do {
            try process.run()
            bridgeProcess = process
            return true
        } catch {
            showError("The local bridge could not be launched: \(error.localizedDescription)")
            return false
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
        if isPenguinRetryURL(url) {
            beginBridgeCheck()
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
