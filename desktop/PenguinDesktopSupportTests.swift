import Foundation

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else {
        FileHandle.standardError.write(Data("FAIL: \(message)\n".utf8))
        exit(1)
    }
}

@main
struct PenguinDesktopSupportTests {
    static func main() {
        let loading = penguinHTMLPage(
            title: "Opening Penguin",
            detail: "Starting your private local messaging workspace."
        )
        expect(loading.contains("PenguinIcon.png"), "loading state uses the bundled Penguin artwork")
        expect(!loading.contains("penguin://retry"), "loading state does not expose retry")

        let failure = penguinHTMLPage(
            title: "Penguin could not start",
            detail: "Bridge failed <temporarily>.",
            retry: true
        )
        expect(failure.contains("href=\"penguin://retry\""), "failure state links to the native retry action")
        expect(failure.contains("Bridge failed &lt;temporarily&gt;."), "failure details are HTML escaped")
        expect(!failure.contains("onclick="), "retry does not reload the static error document")

        expect(
            isPenguinRetryURL(URL(string: "penguin://retry")!),
            "native retry URL is recognized"
        )
        expect(
            !isPenguinRetryURL(URL(string: "https://example.com/retry")!),
            "web URLs cannot trigger native retry"
        )

        let resources = URL(fileURLWithPath: "/Applications/Penguin.app/Contents/Resources", isDirectory: true)
        expect(
            isAllowedPenguinNavigationURL(
                URL(fileURLWithPath: "/Applications/Penguin.app/Contents/Resources/Onboarding.html"),
                resourceURL: resources
            ),
            "signed bundled setup files may load"
        )
        expect(
            !isAllowedPenguinNavigationURL(
                URL(fileURLWithPath: "/Users/example/private.html"),
                resourceURL: resources
            ),
            "arbitrary local files cannot load"
        )
        expect(
            isAllowedPenguinNavigationURL(URL(string: "http://127.0.0.1:9000/penguin-connect/ui")!, resourceURL: resources),
            "loopback app pages may load"
        )
        expect(
            !isAllowedPenguinNavigationURL(URL(string: "https://example.com")!, resourceURL: resources),
            "external pages cannot replace the app web view"
        )

        let onboarding = penguinOnboardingHTMLPage(initialStep: 2)
        expect(onboarding.contains("let step = 2;"), "onboarding opens at the requested safe step")
        expect(onboarding.contains("http://127.0.0.1:8081/pairing/qr.png"), "pairing QR is loaded only from loopback")
        expect(!onboarding.contains("http://0.0.0.0"), "onboarding does not reference a public local listener")
        expect(onboarding.contains("default-src 'none'"), "onboarding blocks undeclared web content")
        expect(onboarding.contains("name=\"profile\" value=\"read-only\" checked"), "remote access defaults to read only")
        expect(onboarding.contains("name=\"tunnel\" value=\"tailscale\" checked"), "stable Tailscale URLs are the default")
        expect(onboarding.contains("id=\"contacts-button\""), "Contacts permission can adapt after macOS denial")
        expect(onboarding.contains("one-time Mac approval"), "Contacts setup explains that approval is not per action")
        expect(onboarding.contains("every remote request needs today's access code"), "daily access code is disclosed before setup")
        expect(onboarding.contains("id=\"daily-code\""), "today's access code has a visible local display")
        expect(onboarding.contains("id=\"bluebubbles-url\""), "enhanced iMessage setup has an explicit loopback URL")
        expect(onboarding.contains("id=\"bluebubbles-password\""), "enhanced iMessage setup accepts a private password")
        expect(onboarding.contains("System Integrity Protection"), "enhanced iMessage setup warns about its Mac security tradeoff")
        expect(onboarding.contains("post('configureBlueBubbles'"), "enhanced iMessage setup is handled by the native app")

        let bounded = penguinOnboardingHTMLPage(initialStep: 999)
        expect(bounded.contains("let step = 4;"), "onboarding bounds untrusted initial step values")
    }
}
