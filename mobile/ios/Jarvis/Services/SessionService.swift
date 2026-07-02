import Foundation

class SessionService {
    static let shared = SessionService()
    private init() {}

    func createSession(personId: UUID? = nil) async throws -> SessionOut {
        var req = try authedRequest(path: "/sessions", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let personId {
            req.httpBody = try JSONEncoder().encode(["person_id": personId.uuidString])
        } else {
            req.httpBody = "{}".data(using: .utf8)
        }
        let (data, _) = try await URLSession.shared.data(for: req)
        return try JSONDecoder().decode(SessionOut.self, from: data)
    }

    func endSession(_ sessionId: UUID) async throws {
        let req = try authedRequest(path: "/sessions/\(sessionId)/end", method: "POST")
        _ = try await URLSession.shared.data(for: req)
    }

    func createPerson(name: String) async throws -> PersonOut {
        var req = try authedRequest(path: "/people", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["name": name])
        let (data, _) = try await URLSession.shared.data(for: req)
        return try JSONDecoder().decode(PersonOut.self, from: data)
    }

    func enrollPerson(_ personId: UUID, audioData: Data) async throws {
        var req = try authedRequest(path: "/people/\(personId)/enroll", method: "POST")
        let boundary = "Boundary-\(UUID().uuidString)"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"audio\"; filename=\"voice.m4a\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/m4a\r\n\r\n".data(using: .utf8)!)
        body.append(audioData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        req.httpBody = body
        _ = try await URLSession.shared.data(for: req)
    }

    private func authedRequest(path: String, method: String = "GET") throws -> URLRequest {
        guard let token = KeychainHelper.load(forKey: "access_token") else {
            throw ServiceError.noToken
        }
        var req = URLRequest(url: URL(string: Config.apiBaseURL + path)!)
        req.httpMethod = method
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return req
    }

    enum ServiceError: LocalizedError {
        case noToken
        var errorDescription: String? { "Not signed in" }
    }
}
