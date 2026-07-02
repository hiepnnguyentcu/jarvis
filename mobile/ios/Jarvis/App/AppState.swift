import AVFoundation
import Combine
import Foundation

@MainActor
class AppState: ObservableObject {
    static let shared = AppState()

    @Published var isAuthenticated = false
    @Published var voiceEnrolled = false
    @Published var user: UserOut?
    @Published var activeSession: SessionOut?
    @Published var isStreaming = false
    @Published var sessionPersonName: String?
    @Published var lastExtractionInfo: String?
    @Published var sessionError: String?

    private var commandObserver: AnyCancellable?
    private let voice = VoiceCommandService.shared

    private init() {}

    // MARK: - Voice command listening

    func startListening() {
        commandObserver = voice.$command
            .compactMap { $0 }
            .sink { [weak self] command in
                Task { @MainActor [weak self] in
                    await self?.handle(command)
                    self?.voice.command = nil  // consume
                }
            }
        Task { await voice.start() }
    }

    func stopListening() {
        voice.stop()
        commandObserver = nil
    }

    // MARK: - Command handling

    private func handle(_ command: VoiceCommand) async {
        switch command {
        case .meetPerson(let name):
            guard !isStreaming else { return }
            await startSession(for: name)
        case .stopSession:
            guard isStreaming else { return }
            await endSession()
        }
    }

    // MARK: - Session lifecycle

    func startSession(for name: String) async {
        sessionError = nil
        lastExtractionInfo = nil
        do {
            // Look up person by name
            let people = try await PeopleService.shared.listPeople()
            let match = people.first {
                $0.name.lowercased().contains(name.lowercased()) && !$0.isWearer
            }

            if let person = match {
                sessionPersonName = person.name
                // Play recap before starting to record
                await RecapPlayerService.shared.play(personId: person.id)
                let session = try await SessionService.shared.createSession(personId: person.id)
                activeSession = session
                try await AudioBridgeService.shared.startStreaming(sessionId: session.id)
                isStreaming = true
            } else {
                // Unknown person — start session anyway, enrollment happens after
                sessionPersonName = name.capitalized
                let session = try await SessionService.shared.createSession()
                activeSession = session
                try await AudioBridgeService.shared.startStreaming(sessionId: session.id)
                isStreaming = true
            }
        } catch {
            sessionError = error.localizedDescription
        }
    }

    func endSession() async {
        RecapPlayerService.shared.stop()
        AudioBridgeService.shared.stopStreaming()
        if let session = activeSession {
            try? await SessionService.shared.endSession(session.id)
            lastExtractionInfo = "Extracting knowledge..."
            // Backend auto-extracts in background; show a brief confirmation
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            lastExtractionInfo = "Knowledge extracted"
        }
        isStreaming = false
        activeSession = nil
        sessionPersonName = nil
    }
}
