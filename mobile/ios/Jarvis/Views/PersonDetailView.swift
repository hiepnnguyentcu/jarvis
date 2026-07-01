import SwiftUI

struct PersonDetailView: View {
    let person: PersonOut

    @State private var graph: GraphOut?
    @State private var recap: String?
    @State private var isLoadingGraph = false
    @State private var isLoadingRecap = false
    @State private var error: String?
    @State private var showFullGraph = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                graphSection
                recapSection
                edgeListSection
            }
            .padding()
        }
        .navigationTitle(person.name)
        .navigationBarTitleDisplayMode(.large)
        .task { await loadAll() }
    }

    // MARK: - Sections

    private var graphSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Knowledge Graph", systemImage: "point.3.connected.trianglepath.dotted")
                .font(.headline)

            if isLoadingGraph {
                ProgressView()
                    .frame(maxWidth: .infinity, minHeight: 260)
            } else if let graph, !graph.nodes.isEmpty {
                ZStack(alignment: .topTrailing) {
                    GraphCanvasView(graph: graph)
                        .frame(height: 320)
                        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
                        .clipped()

                    Button {
                        showFullGraph = true
                    } label: {
                        Image(systemName: "arrow.up.left.and.arrow.down.right")
                            .padding(8)
                            .background(.ultraThinMaterial, in: Circle())
                    }
                    .padding(10)
                }
                .sheet(isPresented: $showFullGraph) {
                    NavigationStack {
                        GraphCanvasView(graph: graph)
                            .navigationTitle("\(person.name) · \(graph.nodes.count) nodes")
                            .navigationBarTitleDisplayMode(.inline)
                            .toolbar {
                                ToolbarItem(placement: .confirmationAction) {
                                    Button("Done") { showFullGraph = false }
                                }
                            }
                    }
                    .interactiveDismissDisabled(true)
                }
            } else {
                emptyState(
                    icon: "point.3.connected.trianglepath.dotted",
                    message: "No graph facts yet.\nRun a session and extract knowledge."
                )
            }
        }
    }

    private var recapSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("Recap", systemImage: "text.bubble")
                    .font(.headline)
                Spacer()
                Button(action: { Task { await loadRecap() } }) {
                    Image(systemName: "arrow.clockwise")
                        .font(.caption)
                }
                .disabled(isLoadingRecap)
            }

            if isLoadingRecap {
                ProgressView()
                    .frame(maxWidth: .infinity, minHeight: 60)
            } else if let recap, !recap.isEmpty {
                Text(recap)
                    .font(.body)
                    .foregroundColor(.primary)
                    .padding()
                    .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
            } else {
                emptyState(
                    icon: "text.bubble",
                    message: "No recap available yet. Recap appears after\na session with extracted knowledge."
                )
            }
        }
    }

    private var edgeListSection: some View {
        Group {
            if let graph, !graph.edges.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Facts (\(graph.edges.count))", systemImage: "list.bullet")
                        .font(.headline)

                    VStack(spacing: 6) {
                        ForEach(Array(graph.edges.enumerated()), id: \.offset) { _, edge in
                            FactRow(nodes: graph.nodes, edge: edge)
                        }
                    }
                }
            }
        }
    }

    // MARK: - Helpers

    private func emptyState(icon: String, message: String) -> some View {
        VStack(spacing: 8) {
            Image(systemName: icon)
                .font(.largeTitle)
                .foregroundColor(.secondary.opacity(0.4))
            Text(message)
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, minHeight: 80)
        .padding()
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Load

    private func loadAll() async {
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await loadGraph() }
            group.addTask { await loadRecap() }
        }
    }

    private func loadGraph() async {
        isLoadingGraph = true
        do {
            graph = try await PeopleService.shared.getGraph(personId: person.id)
        } catch {
            self.error = error.localizedDescription
        }
        isLoadingGraph = false
    }

    private func loadRecap() async {
        isLoadingRecap = true
        do {
            recap = try await PeopleService.shared.getRecap(personId: person.id)
        } catch {
            // recap failing (no facts yet) is expected — don't surface as error
        }
        isLoadingRecap = false
    }
}

// MARK: - Fact row

private struct FactRow: View {
    let nodes: [GraphNode]
    let edge: GraphEdge

    private func nodeName(_ id: String) -> String {
        nodes.first { $0.id == id }?.name ?? id.prefix(8).description
    }

    var body: some View {
        HStack(spacing: 6) {
            Text(nodeName(edge.from))
                .font(.caption.bold())
                .foregroundColor(.blue)
                .lineLimit(1)

            Image(systemName: "arrow.right")
                .font(.system(size: 9))
                .foregroundColor(.secondary)

            Text(edge.predicate.replacingOccurrences(of: "_", with: " "))
                .font(.caption)
                .foregroundColor(.secondary)
                .lineLimit(1)

            Image(systemName: "arrow.right")
                .font(.system(size: 9))
                .foregroundColor(.secondary)

            Text(nodeName(edge.to))
                .font(.caption.bold())
                .foregroundColor(.green)
                .lineLimit(1)

            Spacer()

            if let conf = edge.confidence {
                Text("\(Int(conf * 100))%")
                    .font(.system(size: 9))
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color(.tertiarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
    }
}
