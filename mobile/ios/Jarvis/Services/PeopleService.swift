import Foundation

class PeopleService {
    static let shared = PeopleService()
    private init() {}

    func listPeople() async throws -> [PersonOut] {
        let req = try authedRequest("/people")
        let (data, _) = try await URLSession.shared.data(for: req)
        return try JSONDecoder().decode([PersonOut].self, from: data)
    }

    func getGraph(personId: UUID) async throws -> GraphOut {
        let req = try authedRequest("/people/\(personId)/graph")
        let (data, _) = try await URLSession.shared.data(for: req)
        return try JSONDecoder().decode(GraphOut.self, from: data)
    }

    func getRecap(personId: UUID) async throws -> String {
        let req = try authedRequest("/people/\(personId)/recap")
        let (data, _) = try await URLSession.shared.data(for: req)
        let obj = try JSONDecoder().decode([String: String].self, from: data)
        return obj["recap"] ?? ""
    }

    func extractKnowledge(sessionId: UUID) async throws -> ExtractionResult {
        var req = try authedRequest("/sessions/\(sessionId)/extract")
        req.httpMethod = "POST"
        let (data, resp) = try await URLSession.shared.data(for: req)
        if let http = resp as? HTTPURLResponse, http.statusCode >= 400 {
            let detail = (try? JSONDecoder().decode([String: String].self, from: data))?["detail"]
            throw ServiceError.backendError(detail ?? "Unknown error (HTTP \(http.statusCode))")
        }
        return try JSONDecoder().decode(ExtractionResult.self, from: data)
    }

    private func authedRequest(_ path: String) throws -> URLRequest {
        guard let token = KeychainHelper.load(forKey: "access_token") else {
            throw ServiceError.noToken
        }
        var req = URLRequest(url: URL(string: Config.apiBaseURL + path)!)
        req.httpMethod = "GET"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return req
    }

    enum ServiceError: LocalizedError {
        case noToken
        case backendError(String)
        var errorDescription: String? {
            switch self {
            case .noToken: return "Not signed in"
            case .backendError(let msg): return msg
            }
        }
    }
}
