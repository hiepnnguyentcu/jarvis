import AVFoundation
import Foundation

enum AudioSource {
    case phoneMic
    case glassesMic(String)
}

@MainActor
class AudioBridgeService: ObservableObject {
    static let shared = AudioBridgeService()

    @Published var segments: [WSSegment] = []
    @Published var hasUnknownSpeaker = false
    @Published private(set) var audioSource: AudioSource = .phoneMic

    private var converter: AVAudioConverter?
    private var webSocketTask: URLSessionWebSocketTask?
    private var enrollmentRecorder: AVAudioRecorder?
    private var enrollmentURL: URL?
    private var routeChangeObserver: NSObjectProtocol?

    private let targetFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: 16000,
        channels: 1,
        interleaved: true
    )!

    private init() {}

    // MARK: - Streaming

    func startStreaming(sessionId: UUID) async throws {
        guard let token = KeychainHelper.load(forKey: "access_token") else { return }

        let granted = await AVAudioApplication.requestRecordPermission()
        guard granted else { throw StreamError.micPermissionDenied }

        // AVAudioSession is configured by AudioEngine.startEngine().
        // We just need to prefer the Bluetooth input once the session is active.
        let avSession = AVAudioSession.sharedInstance()
        preferBluetoothInput(avSession)

        routeChangeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
            Task { @MainActor [weak self] in
                self?.handleRouteChange(avSession: avSession, reason: reason)
            }
        }

        let wsURL = URL(string: "\(Config.wsBaseURL)/ws/stream/\(sessionId)?token=\(token)")!
        webSocketTask = URLSession.shared.webSocketTask(with: wsURL)
        webSocketTask?.resume()

        // Build converter from engine input format
        let engineFormat = AudioEngine.shared.inputFormat
        converter = AVAudioConverter(from: engineFormat, to: targetFormat)

        try await AudioEngine.shared.addConsumer(id: "websocket") { [weak self] buffer in
            self?.sendBuffer(buffer)
        }

        receiveLoop()
    }

    func stopStreaming() {
        AudioEngine.shared.removeConsumer(id: "websocket")
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketTask = nil
        if let obs = routeChangeObserver {
            NotificationCenter.default.removeObserver(obs)
            routeChangeObserver = nil
        }
        audioSource = .phoneMic
        // Do NOT deactivate AVAudioSession here — VoiceCommandService may
        // still have the engine running for command detection.
    }

    func reset() {
        segments = []
        hasUnknownSpeaker = false
    }

    // MARK: - Bluetooth input selection

    private func preferBluetoothInput(_ avSession: AVAudioSession) {
        guard let inputs = avSession.availableInputs else { return }
        let bluetooth = inputs.first(where: { $0.portType == .bluetoothHFP })
        if let bt = bluetooth {
            try? avSession.setPreferredInput(bt)
            audioSource = .glassesMic(bt.portName)
            print("[Audio] using glasses mic: \(bt.portName)")
        } else {
            audioSource = .phoneMic
            print("[Audio] no Bluetooth HFP input found — using phone mic")
        }
    }

    private func handleRouteChange(avSession: AVAudioSession, reason: UInt?) {
        let reasonValue = reason.flatMap { AVAudioSession.RouteChangeReason(rawValue: $0) }
        switch reasonValue {
        case .newDeviceAvailable:
            preferBluetoothInput(avSession)
        case .oldDeviceUnavailable:
            audioSource = .phoneMic
            print("[Audio] Bluetooth disconnected — fell back to phone mic")
        default:
            preferBluetoothInput(avSession)
        }
    }

    // MARK: - Buffer → WebSocket

    private func sendBuffer(_ buffer: AVAudioPCMBuffer) {
        guard let converter else { return }
        let chunkFrames = AVAudioFrameCount(targetFormat.sampleRate * 0.02)
        guard let out = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: chunkFrames) else { return }
        var error: NSError?
        var consumed = false
        converter.convert(to: out, error: &error) { _, status in
            if !consumed { consumed = true; status.pointee = .haveData; return buffer }
            status.pointee = .noDataNow; return nil
        }
        guard error == nil, out.frameLength > 0, let channel = out.int16ChannelData else { return }
        let data = Data(bytes: channel[0], count: Int(out.frameLength) * 2)
        webSocketTask?.send(.data(data)) { _ in }
    }

    // MARK: - Receive segments

    private func receiveLoop() {
        webSocketTask?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(.string(let text)):
                if let data = text.data(using: .utf8),
                   let seg = try? JSONDecoder().decode(WSSegment.self, from: data) {
                    Task { @MainActor in
                        if seg.type == "segment" {
                            self.segments.append(seg)
                            if seg.speakerRole == nil { self.hasUnknownSpeaker = true }
                        }
                    }
                }
                Task { @MainActor in self.receiveLoop() }
            case .success(.data):
                Task { @MainActor in self.receiveLoop() }
            case .failure:
                break
            @unknown default:
                break
            }
        }
    }

    // MARK: - Enrollment recording

    func startEnrollmentRecording() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("enrollment.m4a")
        enrollmentURL = url
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 44100,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue
        ]
        try AVAudioSession.sharedInstance().setCategory(.record)
        try AVAudioSession.sharedInstance().setActive(true)
        enrollmentRecorder = try AVAudioRecorder(url: url, settings: settings)
        enrollmentRecorder?.record()
    }

    func stopEnrollmentRecording() throws -> Data {
        enrollmentRecorder?.stop()
        enrollmentRecorder = nil
        guard let url = enrollmentURL else { throw RecordingError.noFile }
        defer { try? FileManager.default.removeItem(at: url) }
        return try Data(contentsOf: url)
    }

    enum RecordingError: LocalizedError {
        case noFile
        var errorDescription: String? { "Recording file not found" }
    }

    enum StreamError: LocalizedError {
        case micPermissionDenied
        var errorDescription: String? { "Microphone access denied. Go to Settings → Jarvis → Microphone." }
    }
}
