import AVFoundation

@MainActor
final class AudioEngine {
    static let shared = AudioEngine()

    private let engine = AVAudioEngine()
    private var consumers: [String: (AVAudioPCMBuffer) -> Void] = [:]
    private var isRunning = false

    var inputFormat: AVAudioFormat {
        engine.inputNode.outputFormat(forBus: 0)
    }

    private init() {}

    func addConsumer(id: String, _ handler: @escaping (AVAudioPCMBuffer) -> Void) throws {
        consumers[id] = handler
        if !isRunning {
            try startEngine()
        }
    }

    func removeConsumer(id: String) {
        consumers.removeValue(forKey: id)
        if consumers.isEmpty {
            stopEngine()
        }
    }

    // MARK: - Private

    private func startEngine() throws {
        // AVAudioSession must be active before reading inputNode format.
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetooth, .allowBluetoothA2DP])
        try session.setActive(true)

        let node = engine.inputNode
        let fmt = node.outputFormat(forBus: 0)
        node.installTap(onBus: 0, bufferSize: 4096, format: fmt) { [weak self] buffer, _ in
            guard let self else { return }
            let snapshot = self.consumers.values
            for handler in snapshot { handler(buffer) }
        }
        try engine.start()
        isRunning = true
    }

    private func stopEngine() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false)
        isRunning = false
    }
}
