import Foundation

class AuthService {
    static let shared = AuthService()
    private init() {}

    func login(email: String, password: String) async throws -> TokenResponse {
        let tokens = try await post("/auth/login", body: ["email": email, "password": password], as: TokenResponse.self)
        storeTokens(tokens)
        return tokens
    }

    func register(email: String, password: String, firstName: String = "", lastName: String = "") async throws -> TokenResponse {
        let tokens = try await post("/auth/register", body: ["email": email, "password": password, "first_name": firstName, "last_name": lastName], as: TokenResponse.self)
        storeTokens(tokens)
        return tokens
    }

    func me() async throws -> UserOut {
        var req = try authedRequest(path: "/auth/me")
        req.httpMethod = "GET"
        let (data, resp) = try await URLSession.shared.data(for: req)
        if let http = resp as? HTTPURLResponse, http.statusCode == 401 {
            try await refresh()
            return try await me()
        }
        return try decode(UserOut.self, from: data)
    }

    func refresh() async throws {
        guard let refreshToken = KeychainHelper.load(forKey: "refresh_token") else {
            throw AuthError.noToken
        }
        let tokens = try await post("/auth/refresh", body: ["refresh_token": refreshToken], as: TokenResponse.self)
        storeTokens(tokens)
    }

    func enrollVoice(audioData: Data) async throws {
        var req = try authedRequest(path: "/auth/enroll-voice")
        req.httpMethod = "POST"
        let (body, contentType) = multipart(data: audioData, name: "audio", filename: "enrollment.m4a", mimeType: "audio/m4a")
        req.setValue(contentType, forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        let (_, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200...204).contains(http.statusCode) else {
            throw AuthError.enrollmentFailed
        }
    }

    func logout() {
        KeychainHelper.delete(forKey: "access_token")
        KeychainHelper.delete(forKey: "refresh_token")
    }

    // MARK: - Helpers

    private func storeTokens(_ tokens: TokenResponse) {
        KeychainHelper.save(tokens.accessToken, forKey: "access_token")
        KeychainHelper.save(tokens.refreshToken, forKey: "refresh_token")
    }

    private func authedRequest(path: String) throws -> URLRequest {
        guard let token = KeychainHelper.load(forKey: "access_token") else {
            throw AuthError.noToken
        }
        var req = URLRequest(url: URL(string: Config.apiBaseURL + path)!)
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return req
    }

    private func post<T: Decodable>(_ path: String, body: [String: String], as type: T.Type) async throws -> T {
        var req = URLRequest(url: URL(string: Config.apiBaseURL + path)!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let (data, _) = try await URLSession.shared.data(for: req)
        return try decode(type, from: data)
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        try JSONDecoder().decode(type, from: data)
    }

    private func multipart(data: Data, name: String, filename: String, mimeType: String) -> (Data, String) {
        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"\(name)\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: \(mimeType)\r\n\r\n".data(using: .utf8)!)
        body.append(data)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        return (body, "multipart/form-data; boundary=\(boundary)")
    }

    enum AuthError: LocalizedError {
        case noToken
        case enrollmentFailed

        var errorDescription: String? {
            switch self {
            case .noToken: return "Not signed in"
            case .enrollmentFailed: return "Voice enrollment failed"
            }
        }
    }
}
