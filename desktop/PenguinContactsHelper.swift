import Contacts
import Foundation

struct PenguinContactMutation: Decodable {
    let matchHandle: String
    let firstName: String
    let lastName: String
    let organization: String
    let phones: [String]
    let emails: [String]
    let phoneLabel: String
    let emailLabel: String

    enum CodingKeys: String, CodingKey {
        case matchHandle = "match_handle"
        case firstName = "first_name"
        case lastName = "last_name"
        case organization
        case phones
        case emails
        case phoneLabel = "phone_label"
        case emailLabel = "email_label"
    }

    var hasIdentity: Bool {
        !firstName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || !lastName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || !organization.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || phones.contains(where: { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty })
            || emails.contains(where: { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty })
    }
}

struct PenguinContactsHelperFailure: Error {
    let code: String
}

func penguinNormalizedPhone(_ value: String) -> String {
    value.unicodeScalars
        .filter { CharacterSet.decimalDigits.contains($0) }
        .map(String.init)
        .joined()
}

func penguinNormalizedEmail(_ value: String) -> String {
    value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
}

private func penguinPhoneLabel(_ value: String) -> String {
    switch value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
    case "home": return CNLabelHome
    case "work": return CNLabelWork
    case "iphone": return CNLabelPhoneNumberiPhone
    case "main": return CNLabelPhoneNumberMain
    case "mobile", "": return CNLabelPhoneNumberMobile
    default: return value
    }
}

private func penguinEmailLabel(_ value: String) -> String {
    switch value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
    case "work": return CNLabelWork
    case "other": return CNLabelOther
    case "home", "": return CNLabelHome
    default: return value
    }
}

private func penguinMatchingContacts(
    _ matchHandle: String,
    store: CNContactStore
) throws -> [CNContact] {
    let cleanHandle = matchHandle.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !cleanHandle.isEmpty else { return [] }
    let emailKey = cleanHandle.contains("@") ? penguinNormalizedEmail(cleanHandle) : ""
    let phoneKey = emailKey.isEmpty ? penguinNormalizedPhone(cleanHandle) : ""
    guard !emailKey.isEmpty || !phoneKey.isEmpty else {
        throw PenguinContactsHelperFailure(code: "invalid_match_handle")
    }

    let keys: [CNKeyDescriptor] = [
        CNContactIdentifierKey as CNKeyDescriptor,
        CNContactGivenNameKey as CNKeyDescriptor,
        CNContactFamilyNameKey as CNKeyDescriptor,
        CNContactOrganizationNameKey as CNKeyDescriptor,
        CNContactPhoneNumbersKey as CNKeyDescriptor,
        CNContactEmailAddressesKey as CNKeyDescriptor,
    ]
    let request = CNContactFetchRequest(keysToFetch: keys)
    var matches: [CNContact] = []
    try store.enumerateContacts(with: request) { contact, _ in
        let emailMatches = !emailKey.isEmpty && contact.emailAddresses.contains {
            penguinNormalizedEmail($0.value as String) == emailKey
        }
        let phoneMatches = !phoneKey.isEmpty && contact.phoneNumbers.contains {
            penguinNormalizedPhone($0.value.stringValue) == phoneKey
        }
        if emailMatches || phoneMatches {
            matches.append(contact)
        }
    }
    return matches
}

func penguinUpsertContact(
    _ mutation: PenguinContactMutation,
    store: CNContactStore
) throws -> String {
    guard mutation.hasIdentity else {
        throw PenguinContactsHelperFailure(code: "contact_requires_identity")
    }

    let cleanMatch = mutation.matchHandle.trimmingCharacters(in: .whitespacesAndNewlines)
    let contact: CNMutableContact
    let save = CNSaveRequest()
    if cleanMatch.isEmpty {
        contact = CNMutableContact()
        save.add(contact, toContainerWithIdentifier: nil)
    } else {
        let matches = try penguinMatchingContacts(cleanMatch, store: store)
        guard !matches.isEmpty else {
            throw PenguinContactsHelperFailure(code: "contact_to_update_not_found")
        }
        guard matches.count == 1 else {
            throw PenguinContactsHelperFailure(code: "ambiguous_contact_match")
        }
        guard let mutable = matches[0].mutableCopy() as? CNMutableContact else {
            throw PenguinContactsHelperFailure(code: "contact_update_failed")
        }
        contact = mutable
        save.update(contact)
    }

    let firstName = mutation.firstName.trimmingCharacters(in: .whitespacesAndNewlines)
    let lastName = mutation.lastName.trimmingCharacters(in: .whitespacesAndNewlines)
    let organization = mutation.organization.trimmingCharacters(in: .whitespacesAndNewlines)
    if !firstName.isEmpty { contact.givenName = firstName }
    if !lastName.isEmpty { contact.familyName = lastName }
    if !organization.isEmpty { contact.organizationName = organization }

    var existingPhones = Set(contact.phoneNumbers.map { penguinNormalizedPhone($0.value.stringValue) })
    for rawPhone in mutation.phones {
        let phone = rawPhone.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalized = penguinNormalizedPhone(phone)
        guard !normalized.isEmpty, !existingPhones.contains(normalized) else { continue }
        contact.phoneNumbers.append(
            CNLabeledValue(label: penguinPhoneLabel(mutation.phoneLabel), value: CNPhoneNumber(stringValue: phone))
        )
        existingPhones.insert(normalized)
    }

    var existingEmails = Set(contact.emailAddresses.map { penguinNormalizedEmail($0.value as String) })
    for rawEmail in mutation.emails {
        let email = penguinNormalizedEmail(rawEmail)
        guard !email.isEmpty, !existingEmails.contains(email) else { continue }
        contact.emailAddresses.append(
            CNLabeledValue(label: penguinEmailLabel(mutation.emailLabel), value: email as NSString)
        )
        existingEmails.insert(email)
    }

    try store.execute(save)
    return contact.identifier
}

#if !PENGUIN_CONTACTS_HELPER_TESTING
private func penguinWriteResponse(_ payload: [String: Any]) {
    guard JSONSerialization.isValidJSONObject(payload),
          let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

private func penguinContactsAuthorized() -> Bool {
    CNContactStore.authorizationStatus(for: .contacts) == .authorized
}

private func penguinContactsStatus() -> String {
    switch CNContactStore.authorizationStatus(for: .contacts) {
    case .authorized: return "authorized"
    case .limited: return "limited"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "not-determined"
    @unknown default: return "restricted"
    }
}

private func penguinRequestContactsAccess() -> Bool {
    if penguinContactsAuthorized() { return true }
    let semaphore = DispatchSemaphore(value: 0)
    var granted = false
    CNContactStore().requestAccess(for: .contacts) { allowed, _ in
        granted = allowed
        semaphore.signal()
    }
    semaphore.wait()
    return granted
}

@main
struct PenguinContactsHelper {
    static func main() {
        let action = CommandLine.arguments.dropFirst().first ?? "--status"
        switch action {
        case "--status":
            let authorized = penguinContactsAuthorized()
            penguinWriteResponse(["success": authorized, "authorized": authorized, "status": penguinContactsStatus()])
            exit(authorized ? 0 : 2)
        case "--authorize":
            let authorized = penguinRequestContactsAccess()
            penguinWriteResponse(["success": authorized, "authorized": authorized, "status": penguinContactsStatus()])
            exit(authorized ? 0 : 2)
        case "--upsert":
            guard penguinContactsAuthorized() else {
                penguinWriteResponse(["success": false, "error": "contacts_permission_required"])
                exit(2)
            }
            do {
                let input = FileHandle.standardInput.readDataToEndOfFile()
                guard input.count <= 65_536 else {
                    throw PenguinContactsHelperFailure(code: "contact_payload_too_large")
                }
                let mutation = try JSONDecoder().decode(PenguinContactMutation.self, from: input)
                let identifier = try penguinUpsertContact(mutation, store: CNContactStore())
                penguinWriteResponse(["success": true, "contact_id": identifier])
                exit(0)
            } catch let failure as PenguinContactsHelperFailure {
                penguinWriteResponse(["success": false, "error": failure.code])
                exit(3)
            } catch {
                penguinWriteResponse(["success": false, "error": "contacts_write_failed"])
                exit(3)
            }
        default:
            penguinWriteResponse(["success": false, "error": "unknown_action"])
            exit(64)
        }
    }
}
#endif
