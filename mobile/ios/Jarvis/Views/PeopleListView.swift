import SwiftUI

struct PeopleListView: View {
    @EnvironmentObject var appState: AppState
    @State private var people: [PersonOut] = []
    @State private var isLoading = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Group {
                if isLoading && people.isEmpty {
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if people.isEmpty {
                    VStack(spacing: 12) {
                        Image(systemName: "person.2.slash")
                            .font(.system(size: 48))
                            .foregroundColor(.secondary.opacity(0.4))
                        Text("No people yet")
                            .font(.headline)
                            .foregroundColor(.secondary)
                        Text("Contacts appear here after a session\nwith an identified speaker.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List(people, id: \.id) { person in
                        NavigationLink(destination: PersonDetailView(person: person)) {
                            PersonRow(person: person)
                        }
                        .swipeActions(edge: .leading) {
                            if !person.isWearer {
                                Button {
                                    Task { await appState.startSession(for: person.name) }
                                } label: {
                                    Label("Start Session", systemImage: "mic.fill")
                                }
                                .tint(.green)
                            }
                        }
                    }
                    .listStyle(.insetGrouped)
                }
            }
            .navigationTitle("People")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: { Task { await load() } }) {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(isLoading)
                }
            }
            .task { await load() }
        }
    }

    private func load() async {
        isLoading = true
        do {
            let fetched = try await PeopleService.shared.listPeople()
            people = fetched.sorted { $0.isWearer && !$1.isWearer }
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}

private struct PersonRow: View {
    let person: PersonOut

    var body: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(Color.blue.opacity(0.15))
                .frame(width: 40, height: 40)
                .overlay(
                    Text(person.name.prefix(1).uppercased())
                        .font(.headline.bold())
                        .foregroundColor(.blue)
                )

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(person.name)
                        .font(.body.bold())
                    if person.isWearer {
                        Text("Wearer")
                            .font(.caption2.bold())
                            .foregroundColor(.white)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.blue, in: Capsule())
                    }
                }

                if person.hasVoiceEmbedding {
                    Label("Voice enrolled", systemImage: "waveform")
                        .font(.caption2)
                        .foregroundColor(.green)
                } else {
                    Label("No voice", systemImage: "waveform.slash")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }

            Spacer()
        }
        .padding(.vertical, 4)
    }
}
