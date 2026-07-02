import Foundation
import MWDATCore
import MWDATDisplay
import MWDATMockDevice

// MARK: - Connection state

enum GlassesConnectionState: Equatable {
    case disconnected
    case registering
    case connecting
    case connected
    case error(String)

    var label: String {
        switch self {
        case .disconnected: return "Disconnected"
        case .registering:  return "Pairing..."
        case .connecting:   return "Connecting..."
        case .connected:    return "Connected"
        case .error(let m): return "Error: \(m)"
        }
    }

    static func == (lhs: Self, rhs: Self) -> Bool {
        switch (lhs, rhs) {
        case (.disconnected, .disconnected), (.registering, .registering),
             (.connecting, .connecting), (.connected, .connected): return true
        case (.error(let a), .error(let b)): return a == b
        default: return false
        }
    }
}

// MARK: - Manager

@MainActor
final class GlassesManager: ObservableObject {
    static let shared = GlassesManager()

    @Published private(set) var connectionState: GlassesConnectionState = .disconnected
    @Published private(set) var deviceName: String?

    private var session: DeviceSession?
    private var sessionMonitor: Task<Void, Never>?

    #if DEBUG
    private var mockGlasses: (any MockGlasses)?
    #endif

    private init() {}

    // MARK: - Lifecycle

    /// Call once at app launch. Reads MWDAT config from Info.plist.
    func configure() {
        try? Wearables.configure()

        #if DEBUG
        MockDeviceKit.shared.enable(config: MockDeviceKitConfig(
            initiallyRegistered: true,
            initialPermissionsGranted: true
        ))
        mockGlasses = try? MockDeviceKit.shared.pairGlasses(model: .rayBanMeta)
        mockGlasses?.don()
        #endif
    }

    /// Trigger the pairing flow.
    /// On a real device this opens the Meta AI app; in the simulator (MockDevice) it connects directly.
    func startRegistration() async {
        connectionState = .registering
        if Wearables.shared.registrationState == .registered {
            await connectSession()
            return
        }
        do {
            try await Wearables.shared.startRegistration()
            await connectSession()
        } catch {
            connectionState = .error(error.localizedDescription)
        }
    }

    /// Handle the deep-link callback URL from the Meta AI app after pairing.
    /// Wire this into `.onOpenURL` in JarvisApp.
    func handleOpenURL(_ url: URL) {
        guard url.scheme == Config.metaURLScheme else { return }
        Task {
            try? await Wearables.shared.handleUrl(url)
            await connectSession()
        }
    }

    func disconnect() {
        sessionMonitor?.cancel()
        sessionMonitor = nil
        session?.stop()
        session = nil
        GlassesDisplayService.shared.unbind()
        connectionState = .disconnected
        deviceName = nil
    }

    // MARK: - Private

    private func connectSession() async {
        connectionState = .connecting
        do {
            let selector = AutoDeviceSelector(wearables: Wearables.shared)
            let newSession = try Wearables.shared.createSession(deviceSelector: selector)
            try newSession.start()
            self.session = newSession

            // Wait for the session to reach .started (or fail to .stopped)
            var started = false
            for await state in newSession.stateStream() {
                if state == .started { started = true; break }
                if state == .stopped { break }
            }

            guard started else {
                connectionState = .error("Could not connect to glasses")
                return
            }

            // Resolve device name
            if let id = Wearables.shared.devices.first,
               let device = Wearables.shared.deviceForIdentifier(id) {
                deviceName = device.name
            } else {
                deviceName = "Ray-Ban Meta"
            }

            // Activate the Display capability
            let display = try newSession.addDisplay()
            display.start()
            GlassesDisplayService.shared.bind(to: display)

            connectionState = .connected

            // Watch for session going away
            sessionMonitor = Task { [weak self] in
                for await state in newSession.stateStream() where state == .stopped {
                    await MainActor.run {
                        self?.connectionState = .disconnected
                        self?.deviceName = nil
                        self?.session = nil
                    }
                    break
                }
            }
        } catch let e as DeviceSessionError {
            connectionState = .error(e.description)
        } catch {
            connectionState = .error(error.localizedDescription)
        }
    }
}
