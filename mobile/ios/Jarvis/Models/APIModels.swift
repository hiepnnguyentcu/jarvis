import Foundation

struct TokenResponse: Codable {
    let accessToken: String
    let refreshToken: String
    let tokenType: String

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case tokenType = "token_type"
    }
}

struct UserOut: Codable {
    let id: UUID
    let email: String
    let firstName: String
    let lastName: String
    let voiceEnrolled: Bool
    let isAdmin: Bool
    let wearerPersonId: UUID?
    let createdAt: String

    var displayName: String {
        let name = "\(firstName) \(lastName)".trimmingCharacters(in: .whitespaces)
        return name.isEmpty ? email : name
    }

    enum CodingKeys: String, CodingKey {
        case id, email
        case firstName = "first_name"
        case lastName = "last_name"
        case voiceEnrolled = "voice_enrolled"
        case isAdmin = "is_admin"
        case wearerPersonId = "wearer_person_id"
        case createdAt = "created_at"
    }
}

struct SessionOut: Codable {
    let id: UUID
    let userId: UUID
    let personId: UUID?
    let startedAt: String
    let endedAt: String?
    let audioR2Key: String?
    let identityConfidence: Double?

    enum CodingKeys: String, CodingKey {
        case id
        case userId = "user_id"
        case personId = "person_id"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case audioR2Key = "audio_r2_key"
        case identityConfidence = "identity_confidence"
    }
}

struct PersonOut: Codable {
    let id: UUID
    let userId: UUID
    let name: String
    let createdAt: String
    let hasVoiceEmbedding: Bool
    let isWearer: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case userId = "user_id"
        case name
        case createdAt = "created_at"
        case hasVoiceEmbedding = "has_voice_embedding"
        case isWearer = "is_wearer"
    }
}

struct GraphNode: Codable, Identifiable {
    let id: String
    let name: String
    let type: String
    let icon: String?
    let depth: Int
}

struct GraphEdge: Codable {
    let from: String
    let predicate: String
    let to: String
    let confidence: Double?
}

struct GraphOut: Codable {
    let personId: String
    let name: String
    let nodes: [GraphNode]
    let edges: [GraphEdge]
    let maxDepth: Int

    enum CodingKeys: String, CodingKey {
        case personId = "person_id"
        case name, nodes, edges
        case maxDepth = "max_depth"
    }
}

struct ExtractionResult: Codable {
    let sessionId: UUID
    let triplesStored: Int

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case triplesStored = "triples_stored"
    }
}

struct WSSegment: Codable, Identifiable {
    var id = UUID()
    let type: String
    let speaker: String?
    let speakerRole: String?
    let text: String?
    let startMs: Int?
    let endMs: Int?

    enum CodingKeys: String, CodingKey {
        case type, speaker, text
        case speakerRole = "speaker_role"
        case startMs = "start_ms"
        case endMs = "end_ms"
    }
}
