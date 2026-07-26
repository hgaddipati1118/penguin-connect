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
    }
}
