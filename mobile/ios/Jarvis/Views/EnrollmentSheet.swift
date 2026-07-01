import SwiftUI

struct EnrollmentSheet: View {
    @Environment(\.dismiss) var dismiss
    @State private var name = ""
    @State private var isSubmitting = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Image(systemName: "person.badge.plus")
                    .font(.system(size: 48))
                    .foregroundColor(.blue)

                Text("Who were you just talking to?")
                    .font(.headline)
                    .multilineTextAlignment(.center)

                TextField("Their name", text: $name)
                    .textFieldStyle(.roundedBorder)
                    .autocorrectionDisabled()

                if let error {
                    Text(error)
                        .foregroundColor(.red)
                        .font(.caption)
                }

                Button(action: { Task { await save() } }) {
                    if isSubmitting {
                        ProgressView().tint(.white)
                    } else {
                        Text("Save Contact")
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || isSubmitting)

                Button("Skip for now") { dismiss() }
                    .foregroundColor(.secondary)
                    .font(.subheadline)
            }
            .padding()
            .navigationTitle("New Contact")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }

    private func save() async {
        isSubmitting = true
        error = nil
        do {
            _ = try await SessionService.shared.createPerson(name: name.trimmingCharacters(in: .whitespaces))
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
        isSubmitting = false
    }
}
