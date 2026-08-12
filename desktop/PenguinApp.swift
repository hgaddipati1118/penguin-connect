import Cocoa
import Contacts
import Darwin
import WebKit

private let defaultPort = 9000

private func resolvedRepoURL() -> URL {
    let configured = Bundle.main.object(forInfoDictionaryKey: "PenguinRepoPath") as? String ?? ""
    if configured == "__BUNDLED__", let resources = Bundle.main.resourceURL {
        return resources.appendingPathComponent("PenguinConnect", isDirectory: true)
    }
    return URL(fileURLWithPath: configured, isDirectory: true)
}

private func penguinProcessEnvironment(repoURL: URL) -> [String: String] {
    var environment = ProcessInfo.processInfo.environment
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PENGUIN_CONNECT_APP_EXECUTABLE"] = Bundle.main.executableURL?.path
    let runtimeDirectory = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/PenguinConnect/runtime", isDirectory: true)
    let packagedPython = runtimeDirectory.appendingPathComponent("venv/bin/python")
    let sourcePython = repoURL.appendingPathComponent("server/venv/bin/python")
    environment["PENGUIN_CONNECT_PYTHON_BIN"] = FileManager.default.isExecutableFile(atPath: packagedPython.path)
        ? packagedPython.path
        : sourcePython.path
    let binURL = repoURL.appendingPathComponent("bin", isDirectory: true)
    let cloudflared = binURL.appendingPathComponent("cloudflared")
    let whatsapp = binURL.appendingPathComponent("whatsapp-bridge")
    if FileManager.default.isExecutableFile(atPath: cloudflared.path) {
        environment["PENGUIN_CONNECT_CLOUDFLARED_BIN"] = cloudflared.path
    }
    if FileManager.default.isExecutableFile(atPath: whatsapp.path) {
        let bridgeDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/PenguinConnect/whatsapp-bridge")
        environment["PENGUIN_CONNECT_WHATSAPP_BRIDGE_BIN"] = whatsapp.path
        environment["PENGUIN_CONNECT_WHATSAPP_BRIDGE_DIR"] = bridgeDirectory.path
        environment["PENGUIN_CONNECT_WHATSAPP_DB_PATH"] = bridgeDirectory
            .appendingPathComponent("store/messages.db").path
    }
    let contactsHelper = Bundle.main.bundleURL
        .appendingPathComponent("Contents/Helpers/PenguinContactsHelper")
    if FileManager.default.isExecutableFile(atPath: contactsHelper.path) {
        environment["PENGUIN_CONNECT_CONTACTS_HELPER_BIN"] = contactsHelper.path
    }
    return environment
}

private func runBackgroundBridgeAgent() -> Never {
    let repoURL = resolvedRepoURL()
    let runner = repoURL.appendingPathComponent("scripts/run_penguin_connect_persistent_bridge.sh")
    guard FileManager.default.isExecutableFile(atPath: runner.path) else {
        fputs("Penguin's persistent bridge runner is missing.\n", stderr)
        exit(1)
    }

    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/bash")
    process.arguments = [runner.path]
    process.currentDirectoryURL = repoURL
    process.environment = penguinProcessEnvironment(repoURL: repoURL)

    signal(SIGTERM, SIG_IGN)
    signal(SIGINT, SIG_IGN)
    let terminationSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global())
    let interruptSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
    terminationSource.setEventHandler { if process.isRunning { process.terminate() } }
    interruptSource.setEventHandler { if process.isRunning { process.interrupt() } }
    terminationSource.resume()
    interruptSource.resume()

    do {
        try process.run()
        process.waitUntilExit()
        terminationSource.cancel()
        interruptSource.cancel()
        exit(process.terminationStatus)
    } catch {
        fputs("Penguin could not start its background bridge: \(error.localizedDescription)\n", stderr)
        exit(1)
    }
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

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var bridgeProcess: Process?
    private var whatsappPairingProcess: Process?
    private var launchAttempted = false
    private var healthAttempts = 0
    private var healthGeneration = 0
    private var remoteSetupProcess: Process?
    private var pairingPollGeneration = 0
    private var showingOnboarding = false

    private lazy var repoURL = resolvedRepoURL()

    private lazy var port = repoPort(at: repoURL)
    private lazy var isPackagedRuntime = FileManager.default.isExecutableFile(
        atPath: repoURL.appendingPathComponent("bin/uv").path
    )
    private lazy var appSupportURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/PenguinConnect", isDirectory: true)
    private lazy var onboardingMarkerURL = appSupportURL.appendingPathComponent("onboarding-v1.complete")
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
        if isPackagedRuntime && !FileManager.default.fileExists(atPath: onboardingMarkerURL.path) {
            showOnboarding(step: 0)
        } else if isPackagedRuntime && !hasFullDiskAccess() {
            showOnboarding(step: 1)
        } else {
            beginBridgeCheck()
        }
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        pairingPollGeneration += 1
        if whatsappPairingProcess?.isRunning == true {
            whatsappPairingProcess?.terminate()
        }
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        if showingOnboarding {
            reportPermissions()
        }
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
        let dailyCodeItem = appMenu.addItem(
            withTitle: "Copy Today’s Access Code",
            action: #selector(copyDailyAccessCode),
            keyEquivalent: ""
        )
        dailyCodeItem.target = self
        let setupItem = appMenu.addItem(
            withTitle: "Penguin Setup…",
            action: #selector(openPenguinSetup),
            keyEquivalent: ","
        )
        setupItem.target = self
        let updatesItem = appMenu.addItem(
            withTitle: "Download Latest Release…",
            action: #selector(openLatestRelease),
            keyEquivalent: ""
        )
        updatesItem.target = self
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
        configuration.userContentController.add(self, name: "penguin")
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
        showingOnboarding = false
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
        process.environment = remoteEnvironment()
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

    private var contactsHelperURL: URL {
        Bundle.main.bundleURL
            .appendingPathComponent("Contents/Helpers/PenguinContactsHelper")
    }

    private func remoteEnvironment() -> [String: String] {
        penguinProcessEnvironment(repoURL: repoURL)
    }

    private func showAlert(title: String, detail: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.alertStyle = title.hasPrefix("Could not") ? .warning : .informational
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    @objc private func pairWhatsApp() {
        showOnboarding(step: 2)
    }

    @objc private func setupRemoteMCP() {
        showOnboarding(step: 3)
    }

    @objc private func copyDailyAccessCode() {
        runPythonScript("penguin_connect_mcp_auth.py", arguments: ["--copy-daily-code"]) { [weak self] ok, output in
            self?.showAlert(
                title: ok ? "Access code copied" : "Could not copy access code",
                detail: ok ? "Today's six-character code is on the clipboard." : output
            )
        }
    }

    @objc private func openPenguinSetup() {
        showOnboarding(step: 0)
    }

    @objc private func openLatestRelease() {
        if let url = URL(string: "https://github.com/hgaddipati1118/penguin-connect/releases/latest") {
            NSWorkspace.shared.open(url)
        }
    }

    private func showOnboarding(step: Int) {
        healthGeneration += 1
        showingOnboarding = true
        webView.loadHTMLString(
            penguinOnboardingHTMLPage(initialStep: step),
            baseURL: Bundle.main.resourceURL
        )
        if isPackagedRuntime && !FileManager.default.isExecutableFile(atPath: runtimePythonURL.path) {
            _ = launchBridge()
        }
    }

    private func callOnboarding(_ method: String, arguments: [Any]) {
        guard showingOnboarding,
              let data = try? JSONSerialization.data(withJSONObject: arguments),
              let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.penguinNative?.\(method).apply(null, \(json))")
    }

    private func hasFullDiskAccess() -> Bool {
        let database = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Messages/chat.db")
        do {
            let handle = try FileHandle(forReadingFrom: database)
            try handle.close()
            return true
        } catch {
            return false
        }
    }

    private func reportPermissions() {
        let diskGranted = hasFullDiskAccess()
        guard FileManager.default.isExecutableFile(atPath: contactsHelperURL.path) else {
            reportAppContactsPermission(diskGranted: diskGranted)
            return
        }
        runProcess(executable: contactsHelperURL, arguments: ["--status"]) { [weak self] ok, output in
            guard let self else { return }
            let state = self.contactsPermissionState(from: output)
            self.callOnboarding("permissions", arguments: [diskGranted, ok, state])
        }
    }

    private func contactsPermissionState(from output: String) -> String {
        guard let data = output.data(using: .utf8),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return "restricted"
        }
        return payload["status"] as? String ?? ((payload["authorized"] as? Bool) == true ? "authorized" : "restricted")
    }

    private func reportAppContactsPermission(diskGranted: Bool) {
        let status = CNContactStore.authorizationStatus(for: .contacts)
        let state: String
        switch status {
        case .authorized:
            state = "authorized"
        case .denied:
            state = "denied"
        case .restricted:
            state = "restricted"
        case .notDetermined:
            state = "not-determined"
        @unknown default:
            state = "restricted"
        }
        callOnboarding("permissions", arguments: [diskGranted, status == .authorized, state])
    }

    private func requestContactsAccess() {
        if FileManager.default.isExecutableFile(atPath: contactsHelperURL.path) {
            runProcess(executable: contactsHelperURL, arguments: ["--status"]) { [weak self] ok, output in
                guard let self else { return }
                let state = self.contactsPermissionState(from: output)
                if !ok && (state == "denied" || state == "restricted" || state == "limited") {
                    if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Contacts") {
                        NSWorkspace.shared.open(url)
                    }
                    return
                }
                self.runProcess(executable: self.contactsHelperURL, arguments: ["--authorize"]) { [weak self] _, _ in
                    self?.reportPermissions()
                }
            }
            return
        }
        let status = CNContactStore.authorizationStatus(for: .contacts)
        if status == .denied || status == .restricted {
            if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Contacts") {
                NSWorkspace.shared.open(url)
            }
            return
        }
        guard status == .notDetermined else {
            reportPermissions()
            return
        }
        CNContactStore().requestAccess(for: .contacts) { [weak self] _, _ in
            DispatchQueue.main.async { self?.reportPermissions() }
        }
    }

    private func whatsAppSourceURL() -> URL {
        let bundled = repoURL.appendingPathComponent("bin/whatsapp-bridge")
        if FileManager.default.isExecutableFile(atPath: bundled.path) {
            return bundled
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("whatsapp-mcp/whatsapp-bridge/whatsapp-bridge")
    }

    private func startWhatsAppPairing() {
        let source = whatsAppSourceURL()
        guard FileManager.default.isExecutableFile(atPath: source.path) else {
            callOnboarding("whatsapp", arguments: ["bridge_missing", "0"])
            return
        }
        let directory = appSupportURL.appendingPathComponent("whatsapp-bridge", isDirectory: true)
        let binary = directory.appendingPathComponent("whatsapp-bridge")
        let logs = appSupportURL.appendingPathComponent("logs", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
            if source.standardizedFileURL != binary.standardizedFileURL {
                if FileManager.default.fileExists(atPath: binary.path) {
                    try FileManager.default.removeItem(at: binary)
                }
                try FileManager.default.copyItem(at: source, to: binary)
                try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: binary.path)
            }
            if whatsappPairingProcess?.isRunning != true {
                let outputURL = logs.appendingPathComponent("whatsapp-pairing.log")
                FileManager.default.createFile(atPath: outputURL.path, contents: nil)
                let output = try FileHandle(forWritingTo: outputURL)
                let process = Process()
                process.executableURL = binary
                process.currentDirectoryURL = directory
                process.standardOutput = output
                process.standardError = output
                try process.run()
                whatsappPairingProcess = process
            }
            beginWhatsAppPolling()
        } catch {
            callOnboarding("whatsapp", arguments: ["error", "0"])
        }
    }

    private func beginWhatsAppPolling() {
        pairingPollGeneration += 1
        pollWhatsApp(generation: pairingPollGeneration, attempt: 0)
    }

    private func pollWhatsApp(generation: Int, attempt: Int) {
        guard generation == pairingPollGeneration, attempt < 190 else { return }
        var healthRequest = URLRequest(url: URL(string: "http://127.0.0.1:8080/health")!)
        healthRequest.timeoutInterval = 1
        URLSession.shared.dataTask(with: healthRequest) { [weak self] _, response, _ in
            guard let self else { return }
            if let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) {
                DispatchQueue.main.async {
                    guard generation == self.pairingPollGeneration else { return }
                    self.callOnboarding("whatsapp", arguments: ["connected", "\(attempt)"])
                }
                return
            }
            var statusRequest = URLRequest(url: URL(string: "http://127.0.0.1:8081/pairing/status")!)
            statusRequest.timeoutInterval = 1
            URLSession.shared.dataTask(with: statusRequest) { [weak self] data, _, _ in
                guard let self else { return }
                let payload = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
                let status = payload?["status"] as? String ?? "starting"
                DispatchQueue.main.async {
                    guard generation == self.pairingPollGeneration else { return }
                    self.callOnboarding("whatsapp", arguments: [status, "\(attempt)"])
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                        self.pollWhatsApp(generation: generation, attempt: attempt + 1)
                    }
                }
            }.resume()
        }.resume()
    }

    private func endpointFromOutput(_ output: String) -> String {
        output.split(whereSeparator: \.isNewline)
            .map(String.init)
            .first(where: { $0.hasPrefix("Endpoint: ") })?
            .replacingOccurrences(of: "Endpoint: ", with: "") ?? ""
    }

    private func runProcess(
        executable: URL,
        arguments: [String],
        environment: [String: String]? = nil,
        standardInput: Data? = nil,
        completion: @escaping (Bool, String) -> Void
    ) {
        let process = Process()
        let output = Pipe()
        let input = standardInput == nil ? nil : Pipe()
        process.executableURL = executable
        process.arguments = arguments
        process.currentDirectoryURL = repoURL
        process.environment = environment
        process.standardOutput = output
        process.standardError = output
        process.standardInput = input
        process.terminationHandler = { finished in
            let data = output.fileHandleForReading.readDataToEndOfFile()
            let detail = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            DispatchQueue.main.async {
                completion(finished.terminationStatus == 0, detail)
            }
        }
        do {
            try process.run()
            if let standardInput, let input {
                input.fileHandleForWriting.write(standardInput)
                try? input.fileHandleForWriting.close()
            }
        } catch {
            try? input?.fileHandleForWriting.close()
            DispatchQueue.main.async { completion(false, error.localizedDescription) }
        }
    }

    private func runPythonScript(
        _ name: String,
        arguments: [String],
        standardInput: Data? = nil,
        completion: @escaping (Bool, String) -> Void
    ) {
        let script = repoURL.appendingPathComponent("scripts/\(name)")
        guard FileManager.default.isExecutableFile(atPath: runtimePythonURL.path),
              FileManager.default.fileExists(atPath: script.path) else {
            completion(false, "Penguin's private runtime is not ready yet.")
            return
        }
        runProcess(
            executable: runtimePythonURL,
            arguments: [script.path] + arguments,
            environment: remoteEnvironment(),
            standardInput: standardInput,
            completion: completion
        )
    }

    private func isLaunchAgentLoaded(_ label: String) -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = ["print", "gui/\(getuid())/\(label)"]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus == 0
        } catch {
            return false
        }
    }

    private func reportRemoteStatus() {
        let mcpActive = isLaunchAgentLoaded("com.penguinconnect.remote-mcp")
        let cloudflareActive = isLaunchAgentLoaded("com.penguinconnect.remote-tunnel")
        runPythonScript("penguin_connect_remote_setup.py", arguments: ["--status"]) { [weak self] ok, output in
            guard let self else { return }
            let endpoint = ok ? self.endpointFromOutput(output) : ""
            let stableFunnel = endpoint.contains(".ts.net:10000/")
            self.callOnboarding(
                "remoteStatus",
                arguments: [mcpActive && (cloudflareActive || stableFunnel) && ok, endpoint]
            )
        }
    }

    private func copyRemoteConnection() {
        runPythonScript("penguin_connect_remote_setup.py", arguments: ["--copy"]) { [weak self] ok, output in
            self?.showAlert(
                title: ok ? "Connection copied" : "Could not copy connection",
                detail: ok ? "Paste the JSON bundle into Slashy's MCP Settings." : output
            )
        }
    }

    private func reportDailyAccessCode() {
        runPythonScript("penguin_connect_mcp_auth.py", arguments: ["--daily-code"]) { [weak self] ok, output in
            let code = ok ? output.trimmingCharacters(in: .whitespacesAndNewlines) : ""
            self?.callOnboarding("dailyCode", arguments: [code])
        }
    }

    private func rotateRemoteToken() {
        runPythonScript("penguin_connect_mcp_auth.py", arguments: ["--rotate"]) { [weak self] ok, output in
            guard let self else { return }
            guard ok else {
                self.showAlert(title: "Could not rotate key", detail: output)
                return
            }
            self.runPythonScript("penguin_connect_remote_setup.py", arguments: ["--copy"]) { [weak self] copied, copyOutput in
                self?.showAlert(
                    title: copied ? "Key rotated and connection copied" : "Key rotated",
                    detail: copied
                        ? "The old key is invalid. Replace the Penguin connection in Slashy with the newly copied bundle."
                        : copyOutput
                )
            }
        }
    }

    private func stopRemoteAccess() {
        runPythonScript("penguin_connect_remote_setup.py", arguments: ["--stop"]) { [weak self] ok, output in
            self?.showAlert(
                title: ok ? "Remote access stopped" : "Could not stop remote access",
                detail: ok ? "The public tunnel and remote MCP service are no longer running." : output
            )
        }
    }

    private func reportBlueBubblesStatus() {
        runPythonScript("penguin_connect_bluebubbles.py", arguments: ["--status"]) { [weak self] ok, output in
            guard let self else { return }
            if ok {
                self.callOnboarding("blueBubblesResult", arguments: [true, true, "Enhanced iMessage group creation is connected."])
            } else if output.contains("[not configured]") {
                self.callOnboarding("blueBubblesResult", arguments: [true, false, "Optional enhanced group creation is off."])
            } else {
                self.callOnboarding("blueBubblesResult", arguments: [false, false, output])
            }
        }
    }

    private func configureBlueBubbles(apiURL: String, password: String) {
        let cleanURL = apiURL.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanPassword = password.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanURL.isEmpty, cleanURL.utf8.count <= 512 else {
            callOnboarding("blueBubblesResult", arguments: [false, false, "Enter a valid loopback BlueBubbles URL."])
            return
        }
        guard !cleanPassword.isEmpty, cleanPassword.utf8.count <= 4096 else {
            callOnboarding("blueBubblesResult", arguments: [false, false, "Enter a valid BlueBubbles server password."])
            return
        }
        runPythonScript(
            "penguin_connect_bluebubbles.py",
            arguments: ["--configure-stdin", cleanURL],
            standardInput: Data((cleanPassword + "\n").utf8)
        ) { [weak self] ok, output in
            self?.callOnboarding(
                "blueBubblesResult",
                arguments: [ok, ok, ok ? "Enhanced iMessage group creation is connected." : output]
            )
        }
    }

    private func disconnectBlueBubbles() {
        runPythonScript("penguin_connect_bluebubbles.py", arguments: ["--disconnect"]) { [weak self] ok, output in
            self?.callOnboarding(
                "blueBubblesResult",
                arguments: [ok, false, ok ? "Enhanced iMessage group creation is off." : output]
            )
        }
    }

    private func markOnboardingCompleteAndOpenPenguin() {
        do {
            try FileManager.default.createDirectory(at: appSupportURL, withIntermediateDirectories: true)
            try Data("complete\n".utf8).write(to: onboardingMarkerURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: onboardingMarkerURL.path)
        } catch {
            showAlert(title: "Could not finish setup", detail: error.localizedDescription)
            return
        }
        beginBridgeCheck()
    }

    private func finishOnboarding() {
        pairingPollGeneration += 1
        guard let pairingProcess = whatsappPairingProcess, pairingProcess.isRunning else {
            markOnboardingCompleteAndOpenPenguin()
            return
        }
        whatsappPairingProcess = nil
        pairingProcess.terminationHandler = { [weak self] _ in
            guard let self else { return }
            let installer = self.repoURL.appendingPathComponent("scripts/install_launchd_whatsapp_bridge.sh")
            self.runProcess(
                executable: URL(fileURLWithPath: "/bin/bash"),
                arguments: [installer.path],
                environment: self.remoteEnvironment()
            ) { [weak self] ok, output in
                guard let self else { return }
                if ok {
                    self.markOnboardingCompleteAndOpenPenguin()
                } else {
                    self.showAlert(title: "Could not keep WhatsApp connected", detail: output)
                }
            }
        }
        pairingProcess.terminate()
    }

    private func runRemoteSetup(profile: String, tunnel: String) {
        guard remoteSetupProcess?.isRunning != true else {
            callOnboarding("remoteResult", arguments: [false, "", "Setup is already running."])
            return
        }
        let python = runtimePythonURL
        let setupScript = repoURL.appendingPathComponent("scripts/penguin_connect_remote_setup.py")
        guard FileManager.default.isExecutableFile(atPath: python.path),
              FileManager.default.fileExists(atPath: setupScript.path) else {
            callOnboarding("remoteResult", arguments: [false, "", "Penguin is still preparing its private runtime. Try again in a moment."])
            return
        }
        if bridgeProcess?.isRunning != true { _ = launchBridge() }
        if let pairingProcess = whatsappPairingProcess, pairingProcess.isRunning {
            whatsappPairingProcess = nil
            pairingProcess.terminationHandler = { [weak self] _ in
                DispatchQueue.main.async {
                    self?.startRemoteSetupProcess(
                        python: python,
                        setupScript: setupScript,
                        profile: profile,
                        tunnel: tunnel
                    )
                }
            }
            pairingProcess.terminate()
            return
        }
        startRemoteSetupProcess(
            python: python,
            setupScript: setupScript,
            profile: profile,
            tunnel: tunnel
        )
    }

    private func startRemoteSetupProcess(
        python: URL,
        setupScript: URL,
        profile: String,
        tunnel: String
    ) {
        let process = Process()
        let output = Pipe()
        process.executableURL = python
        process.arguments = [setupScript.path, "--profile", profile, "--tunnel", tunnel]
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
                    self.callOnboarding("remoteResult", arguments: [true, self.endpointFromOutput(detail), ""])
                } else {
                    self.callOnboarding("remoteResult", arguments: [false, "", detail])
                }
            }
        }
        do {
            try process.run()
            remoteSetupProcess = process
        } catch {
            callOnboarding("remoteResult", arguments: [false, "", error.localizedDescription])
        }
    }

    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        guard showingOnboarding,
              message.frameInfo.isMainFrame,
              message.name == "penguin",
              let body = message.body as? [String: Any],
              let action = body["action"] as? String else { return }

        switch action {
        case "openFullDiskAccess":
            if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") {
                NSWorkspace.shared.open(url)
            }
        case "checkPermissions":
            reportPermissions()
        case "requestContacts":
            requestContactsAccess()
        case "checkWhatsApp":
            beginWhatsAppPolling()
        case "startWhatsApp":
            startWhatsAppPairing()
        case "setupRemote":
            let profile = body["profile"] as? String ?? "read-only"
            let tunnel = body["tunnel"] as? String ?? "tailscale"
            guard profile == "read-only" || profile == "slashy" else {
                callOnboarding("remoteResult", arguments: [false, "", "Unknown access profile."])
                return
            }
            guard tunnel == "tailscale" || tunnel == "cloudflare-quick" else {
                callOnboarding("remoteResult", arguments: [false, "", "Unknown public connection provider."])
                return
            }
            runRemoteSetup(profile: profile, tunnel: tunnel)
        case "remoteStatus":
            reportRemoteStatus()
        case "blueBubblesStatus":
            reportBlueBubblesStatus()
        case "configureBlueBubbles":
            let apiURL = body["apiURL"] as? String ?? ""
            let password = body["password"] as? String ?? ""
            configureBlueBubbles(apiURL: apiURL, password: password)
        case "disconnectBlueBubbles":
            disconnectBlueBubbles()
        case "copyConnection":
            copyRemoteConnection()
        case "dailyCode":
            reportDailyAccessCode()
        case "copyDailyCode":
            copyDailyAccessCode()
        case "rotateToken":
            rotateRemoteToken()
        case "stopRemote":
            stopRemoteAccess()
        case "openSlashy":
            if let url = URL(string: "https://app.slashy.com") {
                NSWorkspace.shared.open(url)
            }
        case "openTailscale":
            if let url = URL(string: "https://tailscale.com/download/mac") {
                NSWorkspace.shared.open(url)
            }
        case "finish":
            finishOnboarding()
        default:
            return
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
        if !isAllowedPenguinNavigationURL(url, resourceURL: Bundle.main.resourceURL) {
            if navigationAction.navigationType == .linkActivated {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }
}

@main
struct PenguinApplication {
    static func main() {
        if CommandLine.arguments.dropFirst().contains("--bridge-agent") {
            runBackgroundBridgeAgent()
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
    }
}
