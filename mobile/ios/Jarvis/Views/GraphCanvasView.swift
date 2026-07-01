import SwiftUI

// MARK: - Main canvas

struct GraphCanvasView: View {
    let graph: GraphOut

    @State private var selectedNode: GraphNode?
    @State private var scale: CGFloat = 1.0
    @State private var panOffset: CGSize = .zero
    @GestureState private var liveScale: CGFloat = 1.0
    @GestureState private var livePan: CGSize = .zero

    private let ringSpacing: CGFloat = 160

    // Positions in graph space (origin = root node)
    private var positions: [String: CGPoint] {
        let grouped = Dictionary(grouping: graph.nodes, by: { $0.depth })
        var result: [String: CGPoint] = [:]
        for (depth, nodes) in grouped {
            let radius = CGFloat(depth) * ringSpacing
            for (i, node) in nodes.enumerated() {
                let count = nodes.count
                let angle: Double = count == 1
                    ? -.pi / 2
                    : 2 * .pi * Double(i) / Double(count) - .pi / 2
                result[node.id] = CGPoint(
                    x: radius * CGFloat(cos(angle)),
                    y: radius * CGFloat(sin(angle))
                )
            }
        }
        return result
    }

    private var effectiveScale: CGFloat { scale * liveScale }
    private var effectivePan: CGSize {
        CGSize(width: panOffset.width + livePan.width,
               height: panOffset.height + livePan.height)
    }

    var body: some View {
        GeometryReader { geo in
            let cx = geo.size.width / 2 + effectivePan.width
            let cy = geo.size.height / 2 + effectivePan.height
            let s  = effectiveScale
            let pos = positions

            ZStack {
                // Depth guide rings
                ForEach(1 ... max(graph.maxDepth, 1), id: \.self) { depth in
                    let diameter = CGFloat(depth) * ringSpacing * s * 2
                    Circle()
                        .stroke(Color.secondary.opacity(0.09), lineWidth: 1)
                        .frame(width: diameter, height: diameter)
                        .position(x: cx, y: cy)
                }

                // Depth labels
                ForEach(1 ... max(graph.maxDepth, 1), id: \.self) { depth in
                    let r = CGFloat(depth) * ringSpacing * s
                    Text("hop \(depth)")
                        .font(.system(size: 9))
                        .foregroundColor(.secondary.opacity(0.35))
                        .position(x: cx, y: cy - r)
                }

                // Edges
                ForEach(Array(graph.edges.enumerated()), id: \.offset) { _, edge in
                    if let fp = pos[edge.from], let tp = pos[edge.to] {
                        let fScreen = CGPoint(x: cx + fp.x * s, y: cy + fp.y * s)
                        let tScreen = CGPoint(x: cx + tp.x * s, y: cy + tp.y * s)
                        let isHighlighted = selectedNode?.id == edge.from || selectedNode?.id == edge.to
                        EdgeLine(
                            from: fScreen,
                            to: tScreen,
                            label: edge.predicate.replacingOccurrences(of: "_", with: " "),
                            isHighlighted: isHighlighted,
                            showLabel: isHighlighted || s > 1.1
                        )
                    }
                }

                // Nodes
                ForEach(graph.nodes) { node in
                    if let p = pos[node.id] {
                        NodeBubble(
                            label: node.name,
                            type: node.type,
                            icon: node.icon,
                            isRoot: node.depth == 0,
                            isSelected: selectedNode?.id == node.id,
                            scale: s
                        )
                        .position(x: cx + p.x * s, y: cy + p.y * s)
                        .onTapGesture {
                            withAnimation(.spring(duration: 0.2)) {
                                selectedNode = selectedNode?.id == node.id ? nil : node
                            }
                        }
                        .zIndex(node.depth == 0 ? 10 : selectedNode?.id == node.id ? 9 : Double(4 - node.depth))
                    }
                }
            }
            .simultaneousGesture(
                SimultaneousGesture(
                    DragGesture(minimumDistance: 6)
                        .updating($livePan) { v, state, _ in state = v.translation }
                        .onEnded { v in
                            panOffset.width += v.translation.width
                            panOffset.height += v.translation.height
                        },
                    MagnificationGesture()
                        .updating($liveScale) { v, state, _ in state = v }
                        .onEnded { v in
                            scale = min(max(scale * v, 0.2), 4.0)
                        }
                )
            )

            // Selected node info card
            if let sel = selectedNode {
                SelectedNodeCard(graph: graph, node: sel) {
                    withAnimation { selectedNode = nil }
                }
                .padding()
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
                .allowsHitTesting(true)
            }
        }
    }
}

// MARK: - Edge

private struct EdgeLine: View {
    let from: CGPoint
    let to: CGPoint
    let label: String
    let isHighlighted: Bool
    let showLabel: Bool

    private var mid: CGPoint {
        CGPoint(x: (from.x + to.x) / 2, y: (from.y + to.y) / 2)
    }

    var body: some View {
        ZStack {
            Path { path in
                path.move(to: from)
                path.addLine(to: to)
            }
            .stroke(
                isHighlighted ? Color.accentColor.opacity(0.7) : Color.secondary.opacity(0.25),
                style: StrokeStyle(lineWidth: isHighlighted ? 1.8 : 1.2, dash: isHighlighted ? [] : [4, 3])
            )

            if showLabel {
                Text(label)
                    .font(.system(size: 8, weight: .medium))
                    .foregroundColor(isHighlighted ? .accentColor : .secondary)
                    .padding(.horizontal, 3)
                    .padding(.vertical, 1)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 3))
                    .position(mid)
            }
        }
    }
}

// MARK: - Node

private struct NodeBubble: View {
    let label: String
    let type: String
    let icon: String?
    let isRoot: Bool
    let isSelected: Bool
    let scale: CGFloat

    private var size: CGFloat {
        let base: CGFloat = isRoot ? 54 : 36
        return base * min(max(scale, 0.5), 1.5)
    }

    private var fontSize: CGFloat { size * 0.28 }

    var body: some View {
        VStack(spacing: 2) {
            Circle()
                .fill(nodeColor.opacity(isSelected ? 1.0 : 0.72))
                .frame(width: size, height: size)
                .overlay(
                    Text(resolvedIcon)
                        .font(.system(size: size * 0.38))
                )
                .shadow(color: nodeColor.opacity(isSelected ? 0.5 : 0.2), radius: isSelected ? 10 : 3)
                .overlay(
                    Circle().stroke(isSelected ? Color.white : Color.clear, lineWidth: 2)
                )

            if scale > 0.5 {
                Text(label)
                    .font(.system(size: max(fontSize, 7), weight: isRoot ? .bold : .regular))
                    .foregroundColor(.primary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .frame(maxWidth: max(size * 2, 60))
            }
        }
    }

    private var nodeColor: Color {
        switch type {
        case "person":   return .blue
        case "company":  return .indigo
        case "place":    return .green
        case "product":  return .purple
        case "role":     return .teal
        case "field":    return .cyan
        case "activity": return .orange
        case "topic":    return .orange
        case "event":    return .pink
        case "animal":   return Color(red: 0.6, green: 0.4, blue: 0.2)
        default:         return .gray
        }
    }

    private var resolvedIcon: String {
        if let icon, !icon.isEmpty, icon != "🔹" { return icon }
        // Type-based fallback for entities without an AI-chosen icon
        switch type {
        case "person":   return "👤"
        case "company":  return "🏢"
        case "place":    return "📍"
        case "product":  return "📱"
        case "role":     return "💼"
        case "field":    return "🎓"
        case "activity": return "🏃"
        case "topic":    return "💡"
        case "event":    return "📅"
        case "animal":   return "🐾"
        default:         return "🔹"
        }
    }
}

// MARK: - Selected node card

private struct SelectedNodeCard: View {
    let graph: GraphOut
    let node: GraphNode
    let onDismiss: () -> Void

    private var outEdges: [GraphEdge] { graph.edges.filter { $0.from == node.id } }
    private var inEdges:  [GraphEdge] { graph.edges.filter { $0.to   == node.id } }

    private func name(for id: String) -> String {
        graph.nodes.first { $0.id == id }?.name ?? id.prefix(8).description
    }

    static func icon(for type: String) -> String {
        switch type {
        case "person":   return "👤"
        case "company":  return "🏢"
        case "place":    return "📍"
        case "product":  return "📱"
        case "role":     return "💼"
        case "field":    return "🎓"
        case "activity": return "🏃"
        case "topic":    return "💡"
        case "event":    return "📅"
        case "animal":   return "🐾"
        default:         return "🔹"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(node.icon ?? Self.icon(for: node.type))
                VStack(alignment: .leading, spacing: 2) {
                    Text(node.name).font(.subheadline.bold())
                    Text("\(node.type) · hop \(node.depth)")
                        .font(.caption2).foregroundColor(.secondary)
                }
                Spacer()
                Button(action: onDismiss) {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundColor(.secondary)
                }
            }

            if !outEdges.isEmpty || !inEdges.isEmpty {
                Divider()
                ForEach(Array(outEdges.prefix(3).enumerated()), id: \.offset) { _, e in
                    EdgeRow(label: e.predicate.replacingOccurrences(of: "_", with: " "),
                            target: name(for: e.to), outgoing: true)
                }
                ForEach(Array(inEdges.prefix(3).enumerated()), id: \.offset) { _, e in
                    EdgeRow(label: e.predicate.replacingOccurrences(of: "_", with: " "),
                            target: name(for: e.from), outgoing: false)
                }
                let extra = (outEdges.count + inEdges.count) - 6
                if extra > 0 {
                    Text("+\(extra) more")
                        .font(.caption2).foregroundColor(.secondary)
                }
            }
        }
        .padding(12)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .shadow(radius: 8)
    }
}

private struct EdgeRow: View {
    let label: String
    let target: String
    let outgoing: Bool

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: outgoing ? "arrow.right" : "arrow.left")
                .font(.system(size: 9))
                .foregroundColor(outgoing ? .accentColor : .secondary)
            Text(label).font(.caption).foregroundColor(.secondary).lineLimit(1)
            Text(target).font(.caption.bold()).lineLimit(1)
        }
    }
}
