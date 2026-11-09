# Android system bars

The Android project is generated; the small app-specific Java bridge is versioned
here and installed by `scripts/sync-android.mjs`. From `services/frontend`:

```sh
npm ci
npm run build
npm run android:add   # first time; generates android/ and installs native sources
# On subsequent builds:
npm run android:sync
cd android && ./gradlew assembleDebug
```

Use the npm commands rather than plain `cap add`/`cap sync` so the activity always
registers `NautionetteSystemBars`. CI uses the same commands. A new APK is required
for native changes; updating the server's web frontend alone does not update an
already-installed app.

`src/system-bars.js` maps the current theme to the visible surfaces: panel and
navigation on mobile lists, canvas on detail/settings screens. It chooses icon
contrast independently for each bar, including custom colors. The native bridge
restores the last colors at startup and reapplies them on resume/configuration
changes.

Android 15+ enforces transparent edge-to-edge bars. Setting only `theme-color` or
`Window.setStatusBarColor` leaves Capacitor's exposed parent background gray. The
bridge paints that background behind the top/bottom margins and also sets legacy
window bar colors for older Android releases. It does not replace Capacitor's
inset listener, change edge-to-edge margins, or resize the keyboard. Android's
contrast scrims are disabled because icon contrast is explicitly selected.

## Verification

- `npm test`: palette/surface mapping, independent icon contrast, alpha handling,
  and browser no-op behavior.
- `npm run test:e2e -- tests/system-bars.spec.js tests/appearance.spec.js`: mocked
  native bridge, preset restoration, route/layout changes, live edits, reload,
  cross-tab synchronization, and failure recovery.
- Device/emulator smoke check (not covered by the mocked bridge): on Android 14
  and Android 15+, check light/dark themes with gesture and three-button
  navigation, opening/closing the keyboard, rotation, and background/resume. Bars
  should match the adjacent surfaces without gray strips or doubled insets.

## Back navigation (iOS, Android and web)

All clients use Vue Router's browser history, not a separate swipe-navigation
stack. Cold detail links seed one parent list entry; reloads preserve existing
history, including agent filters and the chat that opened Settings.

- iOS: `npm run ios:add` / `npm run ios:sync` install
  `ios/BridgeViewController.swift` and wire it into Main.storyboard. It enables
  WKWebView's interactive back/forward gestures. A newly built iOS app is required.
- Android: `@capacitor/app` supplies the system-back handler (gesture and button).
  Quasar uses it to dismiss open dialogs first, then traverse browser history.
  Rebuild/install the APK after syncing; server updates do not add native plugins.
- Web: contain overscroll only on the vertical axis of page/vertical scroll areas.
  Horizontal overscroll containment disables browser back/forward gestures. Keep
  it only on components that intentionally own horizontal gestures, such as graphs.

Regression coverage: `tests/back-navigation.spec.js` exercises back/forward,
scoped chat lists, detail reloads, Settings origins and each section's deep links
in Chromium and WebKit CI. Native sync tests verify generated iOS wiring.
Device smoke check: open a chat from an agent list, edge-swipe back, then forward;
repeat after reload, from a workflow/run/app, and from Settings opened in a chat.
Verify vertical scrolling, horizontal code/graph panning and dialog dismissal are
unaffected. Browser tests cannot synthesize the OS's interactive edge gesture.
