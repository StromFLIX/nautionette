import Capacitor

/// Use WKWebView's interactive history gesture, not a second JS swipe handler.
/// Vue Router owns the same history, including scoped lists and deep links.
class BridgeViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        super.capacitorDidLoad()
        webView?.allowsBackForwardNavigationGestures = true
    }
}
