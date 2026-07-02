import Foundation
import MWDATDisplay

// Use MWDATDisplay.Text explicitly to avoid collision if SwiftUI is imported elsewhere.

/// Renders content on the Meta Ray-Ban Display glasses screen.
@MainActor
final class GlassesDisplayService {
    static let shared = GlassesDisplayService()

    private var display: Display?

    private init() {}

    // MARK: - Binding

    func bind(to display: Display) {
        self.display = display
    }

    func unbind() {
        display = nil
    }

    // MARK: - Content

    /// Push a "since last time" recap to the glasses lens.
    /// Call once speaker identity resolves and recap text is ready.
    func showRecap(for personName: String, text: String) {
        render(
            FlexBox(direction: .column, spacing: 6, alignment: .center) {
                MWDATDisplay.Text(personName, style: .heading, color: .primary)
                MWDATDisplay.Text(text, style: .body, color: .secondary)
            }
        )
    }

    /// Push a one-line status message ("Recording · 0:32", "Sarah Chen identified").
    func showStatus(_ text: String) {
        render(
            FlexBox(direction: .column, alignment: .center) {
                MWDATDisplay.Text(text, style: .meta, color: .secondary)
            }
        )
    }

    func clear() {
        guard let display else { return }
        Task { try? await display.clearDisplay() }
    }

    // MARK: - Private

    private func render(_ view: FlexBox) {
        guard let display else { return }
        Task { try? await display.send(view) }
    }
}
