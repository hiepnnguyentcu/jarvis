import SwiftUI

struct HomeView: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var bridge = AudioBridgeService.shared
    @State private var showEnrollment = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                statusHeader
                    .padding()

                Divider()

                segmentFeed

                Divider()

                bottomBar
                    .padding()
            }
            .navigationTitle("Jarvis")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Sign Out") {
                        appState.stopListening()
                        AuthService.shared.logout()
                        appState.isAuthenticated = false
                    }
                    .font(.caption)
                }
            }
            .sheet(isPresented: $showEnrollment) {
                EnrollmentSheet()
            }
            .onChange(of: appState.isStreaming) { _, streaming in
                if !streaming && bridge.hasUnknownSpeaker {
                    showEnrollment = true
                }
            }
            .task { appState.startListening() }
        }
    }

    // MARK: - Subviews

    private var statusHeader: some View {
        HStack(spacing: 8) {
            if appState.isStreaming {
                Circle()
                    .fill(Color.red)
                    .frame(width: 8, height: 8)
                    .overlay(Circle().stroke(Color.red.opacity(0.4), lineWidth: 4))
                Text("Recording\(appState.sessionPersonName.map { " · \($0)" } ?? "")")
                    .font(.caption.bold())
            } else {
                PulsingMic()
                Text("Say \"Hey Jarvis, I'm about to meet [name]\"")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Spacer()
            if appState.isStreaming {
                audioSourceBadge
            }
        }
    }

    @ViewBuilder
    private var audioSourceBadge: some View {
        switch bridge.audioSource {
        case .phoneMic:
            Label("Phone", systemImage: "iphone")
                .font(.caption2)
                .foregroundColor(.secondary)
        case .glassesMic(let name):
            Label(name, systemImage: "eyeglasses")
                .font(.caption2)
                .foregroundColor(.green)
        }
    }

    private var segmentFeed: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if let info = appState.lastExtractionInfo, !appState.isStreaming {
                        Label(info, systemImage: "brain")
                            .font(.caption)
                            .foregroundColor(.green)
                            .padding(.horizontal)
                            .padding(.top, 8)
                    }
                    if let error = appState.sessionError {
                        Text(error)
                            .font(.caption)
                            .foregroundColor(.red)
                            .padding(.horizontal)
                    }
                    if bridge.segments.isEmpty {
                        Text(appState.isStreaming ? "Listening..." : "Idle — waiting for voice command")
                            .foregroundColor(.secondary)
                            .font(.subheadline)
                            .padding()
                    }
                    ForEach(bridge.segments) { seg in
                        if let text = seg.text, !text.isEmpty {
                            SegmentRow(seg: seg).id(seg.id)
                        }
                    }
                }
                .padding(.horizontal)
                .padding(.vertical, 8)
            }
            .onChange(of: bridge.segments.count) { _, _ in
                if let last = bridge.segments.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    private var bottomBar: some View {
        Group {
            if appState.isStreaming {
                Button(role: .destructive) {
                    Task { await appState.endSession() }
                } label: {
                    Label("Stop", systemImage: "stop.circle")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 4)
                }
                .buttonStyle(.bordered)
                .tint(.red)
            } else {
                Text("Or tap a person in People to start a session")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
            }
        }
    }
}

// MARK: - Pulsing mic indicator

private struct PulsingMic: View {
    @State private var pulse = false

    var body: some View {
        Image(systemName: "mic.fill")
            .font(.caption)
            .foregroundColor(.blue.opacity(0.7))
            .scaleEffect(pulse ? 1.2 : 1.0)
            .animation(.easeInOut(duration: 1).repeatForever(autoreverses: true), value: pulse)
            .onAppear { pulse = true }
    }
}

// MARK: - Segment row

private struct SegmentRow: View {
    let seg: WSSegment

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Text(roleLabel)
                .font(.caption2.bold())
                .foregroundColor(roleColor)
                .frame(width: 54, alignment: .leading)
            Text(seg.text ?? "")
                .font(.subheadline)
                .foregroundColor(.primary)
        }
    }

    private var roleLabel: String {
        switch seg.speakerRole {
        case "wearer": return "You"
        case "other": return seg.speaker ?? "Other"
        default: return seg.speaker ?? "?"
        }
    }

    private var roleColor: Color {
        switch seg.speakerRole {
        case "wearer": return .blue
        case "other": return .green
        default: return .orange
        }
    }
}
