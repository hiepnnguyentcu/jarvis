import AVFoundation
import Speech

enum VoiceCommand {
    case meetPerson(name: String)
    case stopSession
}

@MainActor
final class VoiceCommandService: ObservableObject {
    static let shared = VoiceCommandService()

    @Published var command: VoiceCommand?
    @Published var isListening = false

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var restartTimer: Timer?

    // AVAudioConverter to resample from engine native rate → 16kHz for Speech
    private var converter: AVAudioConverter?
    private let speechFormat = AVAudioFormat(
        commonFormat: .pcmFormatFloat32,
        sampleRate: 16000,
        channels: 1,
        interleaved: false
    )!

    private init() {}

    // MARK: - Start / Stop

    func start() async {
        guard SFSpeechRecognizer.authorizationStatus() == .authorized else {
            await requestPermission()
            return
        }
        guard recognizer?.isAvailable == true else { return }
        do {
            try await AudioEngine.shared.addConsumer(id: "voice-command") { [weak self] buffer in
                self?.feedBuffer(buffer)
            }
            beginRecognition()
            isListening = true
            scheduleRestart()
        } catch {
            print("[Voice] engine start failed: \(error)")
        }
    }

    func stop() {
        restartTimer?.invalidate()
        restartTimer = nil
        endRecognition()
        AudioEngine.shared.removeConsumer(id: "voice-command")
        isListening = false
    }

    // MARK: - Recognition cycle

    private func beginRecognition() {
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        recognitionRequest = request

        recognitionTask = recognizer?.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            if let result {
                let text = result.bestTranscription.formattedString.lowercased()
                self.parse(text)
            }
            if error != nil || (result?.isFinal == true) {
                self.endRecognition()
                if self.isListening { self.beginRecognition() }
            }
        }

        // Set up converter from engine input format to speech format
        let engineFormat = AudioEngine.shared.inputFormat
        if converter == nil || converter?.inputFormat != engineFormat {
            converter = AVAudioConverter(from: engineFormat, to: speechFormat)
        }
    }

    private func endRecognition() {
        recognitionRequest?.endAudio()
        recognitionRequest = nil
        recognitionTask?.cancel()
        recognitionTask = nil
    }

    // Apple limits recognition sessions to ~1 minute; restart proactively at 45s
    private func scheduleRestart() {
        restartTimer?.invalidate()
        restartTimer = Timer.scheduledTimer(withTimeInterval: 45, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.isListening else { return }
                self.endRecognition()
                self.beginRecognition()
            }
        }
    }

    // MARK: - Buffer feeding

    private func feedBuffer(_ buffer: AVAudioPCMBuffer) {
        guard let request = recognitionRequest, let converter else { return }
        let frameCount = AVAudioFrameCount(
            Double(buffer.frameLength) * speechFormat.sampleRate / buffer.format.sampleRate
        )
        guard let out = AVAudioPCMBuffer(pcmFormat: speechFormat, frameCapacity: frameCount) else { return }
        var error: NSError?
        var consumed = false
        converter.convert(to: out, error: &error) { _, status in
            if !consumed { consumed = true; status.pointee = .haveData; return buffer }
            status.pointee = .noDataNow; return nil
        }
        if error == nil, out.frameLength > 0 {
            request.append(out)
        }
    }

    // MARK: - Command parsing

    private var lastCommand: String = ""

    private func parse(_ text: String) {
        guard text != lastCommand else { return }
        lastCommand = text

        let words = text.split(separator: " ").map(String.init)
        guard let jarvisIdx = words.firstIndex(of: "jarvis") else { return }

        // "jarvis stop"
        if words.dropFirst(jarvisIdx + 1).contains("stop") {
            command = .stopSession
            return
        }

        // "jarvis ... meet [name]"
        let after = words.dropFirst(jarvisIdx + 1)
        if let meetIdx = after.firstIndex(of: "meet") {
            let nameWords = after.dropFirst(meetIdx + 1)
            let name = nameWords.prefix(3).joined(separator: " ").trimmingCharacters(in: .whitespaces)
            if !name.isEmpty {
                command = .meetPerson(name: name)
            }
        }
    }

    // MARK: - Permission

    private func requestPermission() async {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { _ in
                continuation.resume()
            }
        }
        if SFSpeechRecognizer.authorizationStatus() == .authorized {
            await start()
        }
    }
}
