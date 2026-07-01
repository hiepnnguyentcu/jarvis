import SwiftUI

struct EnrollmentOnboardingView: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var bridge = AudioBridgeService.shared
    @State private var step: Step = .instructions
    @State private var error: String?
    @State private var secondsLeft = 10

    enum Step { case instructions, recording, uploading, done }

    var body: some View {
        VStack(spacing: 32) {
            Spacer()

            Image(systemName: "waveform.circle.fill")
                .font(.system(size: 72))
                .foregroundColor(.blue)

            Text("Set Up Your Voice")
                .font(.title.bold())

            switch step {
            case .instructions:
                VStack(spacing: 16) {
                    Text("Record 10 seconds of your voice so Jarvis can identify you in conversations.")
                        .multilineTextAlignment(.center)
                        .foregroundColor(.secondary)

                    Button("Start Recording") {
                        Task { await record() }
                    }
                    .buttonStyle(.borderedProminent)
                }

            case .recording:
                VStack(spacing: 16) {
                    Text("Recording...")
                        .font(.headline)
                        .foregroundColor(.red)

                    Text("\(secondsLeft)s remaining")
                        .font(.largeTitle.monospacedDigit())
                        .foregroundColor(.secondary)

                    ProgressView(value: Double(10 - secondsLeft), total: 10)
                        .tint(.red)
                }

            case .uploading:
                VStack(spacing: 12) {
                    ProgressView()
                    Text("Uploading...").foregroundColor(.secondary)
                }

            case .done:
                VStack(spacing: 12) {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 48))
                        .foregroundColor(.green)
                    Text("Voice enrolled").font(.headline)
                }
            }

            if let error {
                Text(error)
                    .foregroundColor(.red)
                    .font(.caption)
                    .multilineTextAlignment(.center)
            }

            Spacer()
        }
        .padding(.horizontal, 32)
    }

    private func record() async {
        error = nil
        do {
            step = .recording
            secondsLeft = 10
            try bridge.startEnrollmentRecording()

            for i in stride(from: 10, through: 1, by: -1) {
                secondsLeft = i
                try await Task.sleep(for: .seconds(1))
            }

            step = .uploading
            let audioData = try bridge.stopEnrollmentRecording()
            try await AuthService.shared.enrollVoice(audioData: audioData)

            step = .done
            try await Task.sleep(for: .seconds(1))
            appState.voiceEnrolled = true
        } catch {
            step = .instructions
            self.error = error.localizedDescription
        }
    }
}
