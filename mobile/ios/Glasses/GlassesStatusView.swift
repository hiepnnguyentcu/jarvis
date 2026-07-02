import SwiftUI

// MARK: - Toolbar badge

/// Small glasses icon + status dot for the HomeView toolbar.
struct GlassesStatusView: View {
    @ObservedObject private var glasses = GlassesManager.shared
    @State private var showSheet = false

    var body: some View {
        Button { showSheet = true } label: {
            HStack(spacing: 3) {
                Image(systemName: "eyeglasses")
                Circle()
                    .fill(dotColor)
                    .frame(width: 6, height: 6)
            }
            .foregroundColor(glasses.connectionState == .connected ? .primary : .secondary)
        }
        .sheet(isPresented: $showSheet) {
            GlassesPairingSheet()
        }
    }

    private var dotColor: Color {
        switch glasses.connectionState {
        case .connected:              return .green
        case .connecting, .registering: return .yellow
        case .error:                  return .red
        case .disconnected:           return Color(.systemGray4)
        }
    }
}

// MARK: - Pairing sheet

struct GlassesPairingSheet: View {
    @ObservedObject private var glasses = GlassesManager.shared
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 28) {
                Spacer()

                Image(systemName: "eyeglasses")
                    .font(.system(size: 72))
                    .foregroundStyle(.blue.opacity(0.85))

                VStack(spacing: 8) {
                    Text("Meta Ray-Ban Glasses")
                        .font(.title2.bold())
                    Text(statusDescription)
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }

                if let name = glasses.deviceName {
                    Label(name, systemImage: "checkmark.circle.fill")
                        .foregroundColor(.green)
                        .font(.subheadline.bold())
                }

                Spacer()

                actionButton
                    .padding(.horizontal, 32)
                    .padding(.bottom, 40)
            }
            .navigationTitle("Glasses")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    @ViewBuilder
    private var actionButton: some View {
        switch glasses.connectionState {
        case .disconnected, .error:
            Button {
                Task { await glasses.startRegistration() }
            } label: {
                Label("Pair Glasses", systemImage: "link.circle.fill")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
            }
            .buttonStyle(.borderedProminent)

        case .registering, .connecting:
            HStack(spacing: 10) {
                ProgressView()
                Text(glasses.connectionState.label)
                    .foregroundColor(.secondary)
            }
            .frame(maxWidth: .infinity)
            .padding()

        case .connected:
            Button(role: .destructive) {
                glasses.disconnect()
            } label: {
                Label("Disconnect", systemImage: "xmark.circle")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
            }
            .buttonStyle(.bordered)
            .tint(.red)
        }
    }

    private var statusDescription: String {
        switch glasses.connectionState {
        case .disconnected:
            return "Pair your Ray-Ban Meta glasses to see recap\ntext directly in your lens."
        case .registering:
            return "Complete pairing in the Meta app, then return here."
        case .connecting:
            return "Establishing connection with your glasses..."
        case .connected:
            return "Connected. Recap text will appear on\nyour glasses display."
        case .error(let msg):
            return msg
        }
    }
}
