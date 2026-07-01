import SwiftUI

@main
struct JarvisApp: App {
    @StateObject private var appState = AppState.shared

    var body: some Scene {
        WindowGroup {
            rootView
                .environmentObject(appState)
                .task { await restoreSession() }
        }
    }

    @ViewBuilder
    private var rootView: some View {
        if appState.isAuthenticated {
            if appState.voiceEnrolled {
                TabView {
                    HomeView()
                        .tabItem { Label("Stream", systemImage: "mic.fill") }
                    PeopleListView()
                        .tabItem { Label("People", systemImage: "person.2.fill") }
                }
            } else {
                EnrollmentOnboardingView()
            }
        } else {
            LoginView()
        }
    }

    private func restoreSession() async {
        do {
            let user = try await AuthService.shared.me()
            appState.user = user
            appState.voiceEnrolled = user.voiceEnrolled
            appState.isAuthenticated = true
        } catch {
            appState.isAuthenticated = false
        }
    }
}
