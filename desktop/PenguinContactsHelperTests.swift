import Foundation

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else {
        FileHandle.standardError.write(Data("FAIL: \(message)\n".utf8))
        exit(1)
    }
}

@main
struct PenguinContactsHelperTests {
    static func main() {
        expect(
            penguinNormalizedPhone("+1 (415) 555-0100") == "14155550100",
            "phone matching uses a stable digits-only identity"
        )
        expect(
            penguinNormalizedEmail(" Person@Example.Test ") == "person@example.test",
            "email matching is case-insensitive and trimmed"
        )

        let valid = PenguinContactMutation(
            matchHandle: "",
            firstName: "Synthetic",
            lastName: "Person",
            organization: "",
            phones: ["+14155550100"],
            emails: ["synthetic@example.test"],
            phoneLabel: "mobile",
            emailLabel: "home"
        )
        expect(valid.hasIdentity, "a synthetic contact payload with identifiers is accepted")

        let empty = PenguinContactMutation(
            matchHandle: "",
            firstName: "",
            lastName: "",
            organization: "",
            phones: [],
            emails: [],
            phoneLabel: "mobile",
            emailLabel: "home"
        )
        expect(!empty.hasIdentity, "an empty contact payload is rejected")
    }
}
