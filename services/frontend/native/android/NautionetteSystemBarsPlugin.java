package dev.nautionette.app;

import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.drawable.Drawable;
import android.os.Build;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/** Colors only: Capacitor and Keyboard retain ownership of webview/IME insets. */
@CapacitorPlugin(name = "NautionetteSystemBars")
public class NautionetteSystemBarsPlugin extends Plugin {
    private int statusColor = Color.rgb(21, 28, 31);
    private int navigationColor = Color.rgb(12, 17, 19);
    private boolean darkStatusIcons = false;
    private boolean darkNavigationIcons = false;
    private SharedPreferences saved;
    private View parent;
    private View.OnLayoutChangeListener layoutListener;

    @Override
    public void load() {
        saved = getContext().getSharedPreferences("nautionette.system-bars", 0);
        statusColor = saved.getInt("statusColor", statusColor);
        navigationColor = saved.getInt("navigationColor", navigationColor);
        darkStatusIcons = saved.getBoolean("darkStatusIcons", false);
        darkNavigationIcons = saved.getBoolean("darkNavigationIcons", false);
        getActivity().runOnUiThread(() -> {
            View webView = getBridge().getWebView();
            parent = (View) webView.getParent();
            // Android 15+ makes the system bars transparent. Color the area
            // exposed by Capacitor's margins, not just the legacy Window bars.
            parent.setBackground(new Drawable() {
                private final Paint paint = new Paint();
                @Override
                public void draw(Canvas canvas) {
                    paint.setColor(statusColor);
                    canvas.drawRect(getBounds(), paint);
                    paint.setColor(navigationColor);
                    canvas.drawRect(getBounds().left, webView.getBottom(),
                        getBounds().right, getBounds().bottom, paint);
                }
                @Override public void setAlpha(int alpha) {}
                @Override public void setColorFilter(ColorFilter filter) {}
                @Override public int getOpacity() { return PixelFormat.OPAQUE; }
            });
            layoutListener = (view, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom) -> parent.invalidate();
            webView.addOnLayoutChangeListener(layoutListener);
            applyAppearance();
        });
    }

    @PluginMethod
    public void setAppearance(PluginCall call) {
        String status = call.getString("statusBarColor", "");
        String navigation = call.getString("navigationBarColor", "");
        // Accept opaque CSS hex only; do not interpret CSS alpha as Android alpha.
        if (!status.matches("#[0-9a-fA-F]{6}") || !navigation.matches("#[0-9a-fA-F]{6}")) {
            call.reject("System bar colors must be opaque #RRGGBB values.");
            return;
        }
        getActivity().runOnUiThread(() -> {
            statusColor = Color.parseColor(status);
            navigationColor = Color.parseColor(navigation);
            darkStatusIcons = call.getBoolean("darkStatusBarIcons", false);
            darkNavigationIcons = call.getBoolean("darkNavigationBarIcons", false);
            applyAppearance();
            // Restore the last appearance before the web bundle starts on launch.
            saved.edit().putInt("statusColor", statusColor).putInt("navigationColor", navigationColor)
                .putBoolean("darkStatusIcons", darkStatusIcons)
                .putBoolean("darkNavigationIcons", darkNavigationIcons).apply();
            call.resolve();
        });
    }

    @SuppressWarnings("deprecation")
    private void applyAppearance() {
        Window window = getActivity().getWindow();
        window.clearFlags(WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS
            | WindowManager.LayoutParams.FLAG_TRANSLUCENT_NAVIGATION);
        window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
        // These still color system bars on Android versions before edge-to-edge enforcement.
        window.setStatusBarColor(statusColor);
        window.setNavigationBarColor(navigationColor);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            window.setStatusBarContrastEnforced(false);
            window.setNavigationBarContrastEnforced(false);
        }
        WindowInsetsControllerCompat controller = WindowCompat.getInsetsController(window, window.getDecorView());
        controller.setAppearanceLightStatusBars(darkStatusIcons);
        controller.setAppearanceLightNavigationBars(darkNavigationIcons);
        if (parent != null) parent.invalidate();
    }

    @Override
    protected void handleOnResume() {
        getActivity().runOnUiThread(this::applyAppearance);
    }

    @Override
    protected void handleOnConfigurationChanged(Configuration configuration) {
        getActivity().runOnUiThread(this::applyAppearance);
    }

    @Override
    protected void handleOnDestroy() {
        if (layoutListener != null) getBridge().getWebView().removeOnLayoutChangeListener(layoutListener);
    }
}
