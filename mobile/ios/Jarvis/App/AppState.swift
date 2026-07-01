import Foundation
import Combine

@MainActor
class AppState: ObservableObject {
    static let shared = AppState()

    @Published var isAuthenticated = false
    @Published var voiceEnrolled = false
    @Published var user: UserOut?
    @Published var activeSession: SessionOut?
    @Published var isStreaming = false

    private init() {}
}
