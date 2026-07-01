import SwiftUI

struct HomeView: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var bridge = AudioBridgeService.shared
    @State private var showEnrollment = false
    @State private var error: String?
    @State private var lastEndedSession: SessionOut?
    @State private var extractionResult: ExtractionResult?
    @State private var isExtracting = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                statusHeader
                    .padding()

                Divider()

                segmentFeed

                Divider()

                controlBar
                    .padding()
            }
            .navigationTitle("Jarvis")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Sign Out") {
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
        }
    }

    // MARK: - Subviews

    private var statusHeader: some View {
        HStack {
            Circle()
                .fill(appState.isStreaming ? Color.green : Color.gray.opacity(0.4))
                .frame(width: 8, height: 8)
            Text(appState.isStreaming ? "Streaming" : "Idle")
                .font(.caption)
                .foregroundColor(.secondary)
            Spacer()
            if let session = appState.activeSession {
                Text(session.id.uuidString.prefix(8))
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .monospacedDigit()
            }
        }
    }

    private var segmentFeed: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if bridge.segments.isEmpty {
                        Text(appState.isStreaming ? "Listening..." : "Tap Start to begin a session")
                            .foregroundColor(.secondary)
                            .font(.subheadline)
                            .padding()
                    }
                    ForEach(bridge.segments) { seg in
                        if let text = seg.text, !text.isEmpty {
                            SegmentRow(seg: seg)
                                .id(seg.id)
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

    private var controlBar: some View {
        VStack(spacing: 10) {
            if let error {
                Text(error)
                    .foregroundColor(.red)
                    .font(.caption)
                    .multilineTextAlignment(.center)
            }

            Button(action: { Task { await toggleSession() } }) {
                Label(
                    appState.isStreaming ? "Stop" : "Start",
                    systemImage: appState.isStreaming ? "stop.circle.fill" : "mic.circle.fill"
                )
                .font(.title2.bold())
                .frame(maxWidth: .infinity)
                .padding(.vertical, 4)
            }
            .buttonStyle(.borderedProminent)
            .tint(appState.isStreaming ? .red : .blue)

            if let session = lastEndedSession, !appState.isStreaming {
                if let result = extractionResult {
                    Label("\(result.triplesStored) facts extracted", systemImage: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundColor(.green)
                } else {
                    Button(action: { Task { await extractKnowledge(session: session) } }) {
                        if isExtracting {
                            ProgressView().tint(.white)
                        } else {
                            Label("Extract Knowledge", systemImage: "brain")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(isExtracting)
                    .font(.subheadline)
                }
            }
        }
    }

    // MARK: - Actions

    private func extractKnowledge(session: SessionOut) async {
        isExtracting = true
        do {
            extractionResult = try await PeopleService.shared.extractKnowledge(sessionId: session.id)
        } catch {
            self.error = "Extraction failed: \(error.localizedDescription)"
        }
        isExtracting = false
    }

    private func toggleSession() async {
        error = nil
        if appState.isStreaming {
            bridge.stopStreaming()
            if let session = appState.activeSession {
                try? await SessionService.shared.endSession(session.id)
                lastEndedSession = session
                extractionResult = nil
            }
            appState.isStreaming = false
            appState.activeSession = nil
        } else {
            lastEndedSession = nil
            extractionResult = nil
            do {
                bridge.reset()
                let session = try await SessionService.shared.createSession()
                appState.activeSession = session
                try await bridge.startStreaming(sessionId: session.id)
                appState.isStreaming = true
            } catch {
                self.error = error.localizedDescription
            }
        }
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
