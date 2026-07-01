import SwiftUI

struct LoginView: View {
    @EnvironmentObject var appState: AppState
    @State private var email = ""
    @State private var password = ""
    @State private var firstName = ""
    @State private var lastName = ""
    @State private var isRegistering = false
    @State private var error: String?
    @State private var isLoading = false

    var body: some View {
        VStack(spacing: 28) {
            Spacer()

            Text("Jarvis")
                .font(.system(size: 42, weight: .bold))

            Text(isRegistering ? "Create an account" : "Sign in")
                .font(.subheadline)
                .foregroundColor(.secondary)

            VStack(spacing: 12) {
                if isRegistering {
                    HStack(spacing: 8) {
                        TextField("First name", text: $firstName)
                            .textFieldStyle(.roundedBorder)
                            .autocorrectionDisabled()
                        TextField("Last name", text: $lastName)
                            .textFieldStyle(.roundedBorder)
                            .autocorrectionDisabled()
                    }
                }

                TextField("Email", text: $email)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.emailAddress)
                    .autocapitalization(.none)
                    .autocorrectionDisabled()

                SecureField("Password", text: $password)
                    .textFieldStyle(.roundedBorder)
            }

            if let error {
                Text(error)
                    .foregroundColor(.red)
                    .font(.caption)
                    .multilineTextAlignment(.center)
            }

            Button(action: { Task { await submit() } }) {
                if isLoading {
                    ProgressView().tint(.white)
                } else {
                    Text(isRegistering ? "Create Account" : "Sign In")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(isLoading || email.isEmpty || password.isEmpty)

            Button(isRegistering ? "Already have an account? Sign in" : "Don't have an account? Register") {
                isRegistering.toggle()
                error = nil
            }
            .font(.caption)
            .foregroundColor(.secondary)

            Spacer()
        }
        .padding(.horizontal, 32)
    }

    private func submit() async {
        isLoading = true
        error = nil
        do {
            if isRegistering {
                _ = try await AuthService.shared.register(email: email, password: password, firstName: firstName, lastName: lastName)
            } else {
                _ = try await AuthService.shared.login(email: email, password: password)
            }
            let user = try await AuthService.shared.me()
            appState.user = user
            appState.voiceEnrolled = user.voiceEnrolled
            appState.isAuthenticated = true
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}
