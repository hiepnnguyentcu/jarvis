import AVFoundation
import Foundation

@MainActor
final class RecapPlayerService {
    static let shared = RecapPlayerService()

    private let synthesizer = AVSpeechSynthesizer()

    private init() {}

    func play(personId: UUID) async {
        do {
            let recap = try await PeopleService.shared.getRecap(personId: personId)
            guard !recap.isEmpty else { return }
            speak(recap)
        } catch {
            print("[Recap] fetch failed: \(error)")
        }
    }

    func stop() {
        synthesizer.stopSpeaking(at: .immediate)
    }

    private func speak(_ text: String) {
        synthesizer.stopSpeaking(at: .immediate)
        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        synthesizer.speak(utterance)
    }
}
