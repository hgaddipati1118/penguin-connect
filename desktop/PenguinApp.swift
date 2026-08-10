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
    private var remoteSetupProcess: Process?

    private lazy var repoURL: URL = {
        let configured = Bundle.main.object(forInfoDictionaryKey: "PenguinRepoPath") as? String ?? ""
        if configured == "__BUNDLED__",
           let resources = Bundle.main.resourceURL {
            return resources.appendingPathComponent("PenguinConnect", isDirectory: true)
        }
        return URL(fileURLWithPath: configured, isDirectory: true)
    }()

    private lazy var port = repoPort(at: repoURL)
    private lazy var isPackagedRuntime = FileManager.default.isExecutableFile(
        atPath: repoURL.appendingPathComponent("scripts/bootstrap_packaged_runtime.sh").path
    )
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
        let pairWhatsAppItem = appMenu.addItem(
            withTitle: "Pair WhatsApp…",
            action: #selector(pairWhatsApp),
            keyEquivalent: ""
        )
        pairWhatsAppItem.target = self
        let remoteMCPItem = appMenu.addItem(
            withTitle: "Connect Slashy MCP…",
            action: #selector(setupRemoteMCP),
            keyEquivalent: ""
        )
        remoteMCPItem.target = self
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
                if self.healthAttempts < (self.isPackagedRuntime ? 450 : 45) {
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
        let packagedRunner = repoURL.appendingPathComponent("scripts/bootstrap_packaged_runtime.sh")
        let developmentRunner = repoURL.appendingPathComponent("scripts/run_penguin_connect_bridge.sh")
        let runner = isPackagedRuntime ? packagedRunner : developmentRunner
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

    private var runtimeDirectory: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/PenguinConnect/runtime", isDirectory: true)
    }

    private var runtimePythonURL: URL {
        let packagedPython = runtimeDirectory.appendingPathComponent("venv/bin/python")
        if FileManager.default.isExecutableFile(atPath: packagedPython.path) {
            return packagedPython
        }
        return repoURL.appendingPathComponent("server/venv/bin/python")
    }

    private func remoteEnvironment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["PENGUIN_CONNECT_PYTHON_BIN"] = runtimePythonURL.path
        let binURL = repoURL.appendingPathComponent("bin", isDirectory: true)
        let cloudflared = binURL.appendingPathComponent("cloudflared")
        let whatsapp = binURL.appendingPathComponent("whatsapp-bridge")
        if FileManager.default.isExecutableFile(atPath: cloudflared.path) {
            environment["PENGUIN_CONNECT_CLOUDFLARED_BIN"] = cloudflared.path
        }
        if FileManager.default.isExecutableFile(atPath: whatsapp.path) {
            environment["PENGUIN_CONNECT_WHATSAPP_BRIDGE_BIN"] = whatsapp.path
            let bridgeDirectory = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Application Support/PenguinConnect/whatsapp-bridge")
            environment["PENGUIN_CONNECT_WHATSAPP_BRIDGE_DIR"] = bridgeDirectory.path
            environment["PENGUIN_CONNECT_WHATSAPP_DB_PATH"] = bridgeDirectory
                .appendingPathComponent("store/messages.db").path
        }
        return environment
    }

    private func showAlert(title: String, detail: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.alertStyle = title.hasPrefix("Could not") ? .warning : .informational
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\"'\"'") + "'"
    }

    @objc private func pairWhatsApp() {
        let bundled = repoURL.appendingPathComponent("bin/whatsapp-bridge")
        let source = FileManager.default.isExecutableFile(atPath: bundled.path)
            ? bundled
            : FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("whatsapp-mcp/whatsapp-bridge/whatsapp-bridge")
        guard FileManager.default.isExecutableFile(atPath: source.path) else {
            showAlert(
                title: "Could not start WhatsApp pairing",
                detail: "The WhatsApp bridge is missing. Install a release build that includes the bridge."
            )
            return
        }
        let destination = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/PenguinConnect/whatsapp-bridge", isDirectory: true)
        let binary = destination.appendingPathComponent("whatsapp-bridge")
        let command = [
            "mkdir -p \(shellQuote(destination.path))",
            "cp \(shellQuote(source.path)) \(shellQuote(binary.path))",
            "chmod 755 \(shellQuote(binary.path))",
            "cd \(shellQuote(destination.path))",
            shellQuote(binary.path),
        ].joined(separator: " && ")
        let script = """
        on run argv
            tell application "Terminal"
                activate
                do script (item 1 of argv)
            end tell
        end run
        """
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", script, command]
        do {
            try process.run()
            showAlert(
                title: "Pair WhatsApp in Terminal",
                detail: "In WhatsApp on your phone, open Linked Devices and scan the QR code. Wait for the Connected message, then you can close Terminal and choose Connect Slashy MCP."
            )
        } catch {
            showAlert(title: "Could not start WhatsApp pairing", detail: error.localizedDescription)
        }
    }

    @objc private func setupRemoteMCP() {
        guard remoteSetupProcess?.isRunning != true else {
            showAlert(title: "Setup is already running", detail: "Finish the current setup before starting another one.")
            return
        }
        let python = runtimePythonURL
        let setupScript = repoURL.appendingPathComponent("scripts/penguin_connect_remote_setup.py")
        guard FileManager.default.isExecutableFile(atPath: python.path),
              FileManager.default.fileExists(atPath: setupScript.path) else {
            showAlert(
                title: "Could not start remote MCP setup",
                detail: "Penguin is still preparing its runtime. Wait until the main window opens, then try again."
            )
            return
        }

        let choice = NSAlert()
        choice.messageText = "Connect Penguin to Slashy?"
        choice.informativeText = "Full access lets Slashy read iMessage and WhatsApp, search Contacts, and request sends or contact changes. Every write still needs a second exact confirmation and your approval on this Mac. Read Only disables all writes."
        choice.addButton(withTitle: "Full Access")
        choice.addButton(withTitle: "Read Only")
        choice.addButton(withTitle: "Cancel")
        let response = choice.runModal()
        guard response != .alertThirdButtonReturn else { return }
        let profile = response == .alertSecondButtonReturn ? "read-only" : "slashy"

        let process = Process()
        let output = Pipe()
        process.executableURL = python
        process.arguments = [setupScript.path, "--profile", profile]
        process.currentDirectoryURL = repoURL
        process.environment = remoteEnvironment()
        process.standardOutput = output
        process.standardError = output
        process.terminationHandler = { [weak self] finished in
            let data = output.fileHandleForReading.readDataToEndOfFile()
            let detail = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            DispatchQueue.main.async {
                guard let self else { return }
                self.remoteSetupProcess = nil
                if finished.terminationStatus == 0 {
                    self.showAlert(
                        title: "Slashy connection copied",
                        detail: detail + "\n\nPaste the copied JSON into Slashy's MCP Settings."
                    )
                } else {
                    self.showAlert(title: "Could not set up remote MCP", detail: detail)
                }
            }
        }
        do {
            try process.run()
            remoteSetupProcess = process
        } catch {
            showAlert(title: "Could not start remote MCP setup", detail: error.localizedDescription)
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
