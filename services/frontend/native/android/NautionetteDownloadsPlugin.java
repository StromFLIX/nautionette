package dev.nautionette.app;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.util.Base64;
import androidx.activity.result.ActivityResult;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.util.UUID;

/** Stage bounded binary chunks privately. The picker call must never contain file bytes:
 * Capacitor persists that call in Android activity state (which has a small Binder limit).
 * No storage permission, public URL, or automatic opening of generated content.
 */
@CapacitorPlugin(name = "NautionetteDownloads")
public class NautionetteDownloadsPlugin extends Plugin {
    private static final int MAX_BASE64_CHUNK = 256 * 1024;
    private File staged;
    private String transferId;
    private long expectedSize;
    private boolean saving;

    @Override
    public void load() {
        // A killed process cannot run handleOnDestroy. Remove its abandoned private
        // staging files when the next plugin instance starts (before any begin).
        getBridge().execute(() -> {
            File[] abandoned = getContext().getCacheDir().listFiles((dir, name) ->
                name.startsWith("nautionette-download-") && name.endsWith(".part"));
            if (abandoned != null) for (File file : abandoned) file.delete();
        });
    }

    // All staging state and file operations are serialized under this plugin's monitor.
    // Disk/provider work runs on the bridge executor, never the UI thread.
    @PluginMethod
    public void begin(PluginCall call) {
        getBridge().execute(() -> {
            synchronized (this) {
                if (staged != null || saving) {
                    call.reject("Another file is being saved. Finish or cancel it first.");
                    return;
                }
                Long size = integer(call, "size");
                if (size == null || size < 0) {
                    call.reject("Missing or invalid file size.");
                    return;
                }
                try {
                    staged = File.createTempFile("nautionette-download-", ".part", getContext().getCacheDir());
                    transferId = UUID.randomUUID().toString();
                    expectedSize = size;
                    JSObject response = new JSObject();
                    response.put("id", transferId);
                    call.resolve(response);
                } catch (IOException error) {
                    clear();
                    call.reject("Could not stage the download.", error);
                }
            }
        });
    }

    @PluginMethod
    public void append(PluginCall call) {
        getBridge().execute(() -> {
            synchronized (this) {
                if (!matches(call) || saving) {
                    call.reject("Download is no longer writable.");
                    return;
                }
                try {
                    String data = call.getString("base64", "");
                    Long offset = integer(call, "offset");
                    if (data.isEmpty() || data.length() > MAX_BASE64_CHUNK || offset == null
                            || offset != staged.length()) throw new IOException("Invalid download chunk.");
                    byte[] bytes = Base64.decode(data, Base64.NO_WRAP);
                    if (staged.length() + bytes.length > expectedSize) throw new IOException("Download exceeds expected size.");
                    try (OutputStream stream = new FileOutputStream(staged, true)) {
                        stream.write(bytes);
                    }
                    call.resolve();
                } catch (Exception error) {
                    clear();
                    call.reject("Could not stage file bytes.", error);
                }
            }
        });
    }

    @PluginMethod
    public synchronized void discard(PluginCall call) {
        if (matches(call) && !saving) clear();
        call.resolve();
    }

    @PluginMethod
    public synchronized void save(PluginCall call) {
        if (!matches(call) || saving || staged.length() != expectedSize) {
            call.reject("Download is incomplete or no longer available.");
            return;
        }
        saving = true;
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType(call.getString("mimeType", "application/octet-stream"));
        intent.putExtra(Intent.EXTRA_TITLE, call.getString("name", "download"));
        try {
            startActivityForResult(call, intent, "fileSelected");
        } catch (Exception error) {
            clear();
            call.reject("Could not open the save-file picker.", error);
        }
    }

    @ActivityCallback
    private void fileSelected(PluginCall call, ActivityResult result) {
        getBridge().execute(() -> {
            synchronized (this) {
                if (call == null) {
                    clear();
                    return;
                }
                if (result.getResultCode() != Activity.RESULT_OK || result.getData() == null
                        || result.getData().getData() == null) {
                    clear();
                    JSObject response = new JSObject();
                    response.put("cancelled", true);
                    call.resolve(response);
                    return;
                }
                Uri destination = result.getData().getData();
                try {
                    if (!matches(call) || staged.length() != expectedSize) {
                        throw new IOException("Staged download was lost. Please retry.");
                    }
                    try (FileInputStream input = new FileInputStream(staged);
                            OutputStream stream = getContext().getContentResolver().openOutputStream(destination, "wt")) {
                        if (stream == null) throw new IOException("No writable destination.");
                        byte[] buffer = new byte[64 * 1024];
                        int count;
                        while ((count = input.read(buffer)) != -1) stream.write(buffer, 0, count);
                        stream.flush();
                    }
                } catch (Exception error) {
                    // Do not leave a truncated image/document behind as a successful download.
                    try { getContext().getContentResolver().delete(destination, null, null); }
                    catch (Exception ignored) { /* Some providers do not support deletion. */ }
                    clear();
                    call.reject("Could not save the file to the selected location.", error);
                    return;
                }
                clear();
                JSObject response = new JSObject();
                response.put("cancelled", false);
                call.resolve(response);
            }
        });
    }

    // Android JSON parses small whole numbers as Integer, not Long. Capacitor's
    // getLong deliberately does not coerce them (including a chunk offset of 0).
    private Long integer(PluginCall call, String key) {
        Long value = call.getLong(key);
        if (value != null) return value;
        Integer small = call.getInt(key);
        return small == null ? null : small.longValue();
    }

    private boolean matches(PluginCall call) {
        return staged != null && transferId != null && transferId.equals(call.getString("id"));
    }

    private void clear() {
        if (staged != null) staged.delete();
        staged = null;
        transferId = null;
        expectedSize = 0;
        saving = false;
    }

    @Override
    protected void handleOnDestroy() {
        getBridge().execute(() -> {
            synchronized (this) { clear(); }
        });
    }
}
