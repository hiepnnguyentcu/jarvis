import Foundation
import AVFoundation

@MainActor
class AudioBridgeService: ObservableObject {
    static let shared = AudioBridgeService()

    @Published var segments: [WSSegment] = []
    @Published var hasUnknownSpeaker = false

    private var engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var webSocketTask: URLSessionWebSocketTask?
    private var enrollmentRecorder: AVAudioRecorder?
    private var enrollmentURL: URL?

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

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .default, options: [.allowBluetooth, .defaultToSpeaker])
        try session.setActive(true)

        let wsURL = URL(string: "\(Config.wsBaseURL)/ws/stream/\(sessionId)?token=\(token)")!
        webSocketTask = URLSession.shared.webSocketTask(with: wsURL)
        webSocketTask?.resume()

        let inputNode = engine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        converter = AVAudioConverter(from: inputFormat, to: targetFormat)

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { [weak self] buffer, _ in
            self?.sendBuffer(buffer)
        }

        try engine.start()
        receiveLoop()
    }

    func stopStreaming() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketTask = nil
        try? AVAudioSession.sharedInstance().setActive(false)
    }

    func reset() {
        segments = []
        hasUnknownSpeaker = false
    }

    // MARK: - Audio tap → WebSocket

    private func sendBuffer(_ buffer: AVAudioPCMBuffer) {
        guard let converter = converter else { return }
        let chunkFrames = AVAudioFrameCount(targetFormat.sampleRate * 0.02) // 20ms
        guard let out = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: chunkFrames) else { return }

        var error: NSError?
        var consumed = false
        converter.convert(to: out, error: &error) { _, status in
            if !consumed {
                consumed = true
                status.pointee = .haveData
                return buffer
            }
            status.pointee = .noDataNow
            return nil
        }

        guard error == nil, out.frameLength > 0, let channel = out.int16ChannelData else { return }
        let byteCount = Int(out.frameLength) * 2
        let data = Data(bytes: channel[0], count: byteCount)
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
                            if seg.speakerRole == nil {
                                self.hasUnknownSpeaker = true
                            }
                        }
                    }
                }
                Task { @MainActor in self.receiveLoop() }
            case .success(.data(_)):
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
