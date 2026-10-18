package dev.nautionette.app;

import android.app.Activity;
import android.content.Intent;
import android.util.Base64;
import androidx.activity.result.ActivityResult;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.OutputStream;

/** Save authenticated bytes through Android's document picker, not a WebView blob URL.
 * No storage permission, public server URL, or automatic opening of generated content.
 */
@CapacitorPlugin(name = "NautionetteDownloads")
public class NautionetteDownloadsPlugin extends Plugin {
    private volatile boolean saving;

    @PluginMethod
    public void save(PluginCall call) {
        if (saving) {
            call.reject("Another file is being saved. Finish or cancel it first.");
            return;
        }
        String name = call.getString("name", "download");
        String data = call.getString("base64");
        if (data == null) {
            call.reject("Missing file data.");
            return;
        }
        saving = true;
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType(call.getString("mimeType", "application/octet-stream"));
        intent.putExtra(Intent.EXTRA_TITLE, name);
        try {
            startActivityForResult(call, intent, "fileSelected");
        } catch (Exception error) {
            saving = false;
            call.reject("Could not open the save-file picker.", error);
        }
    }

    @ActivityCallback
    private void fileSelected(PluginCall call, ActivityResult result) {
        if (call == null) {
            saving = false;
            return;
        }
        if (result.getResultCode() != Activity.RESULT_OK || result.getData() == null
                || result.getData().getData() == null) {
            saving = false;
            JSObject response = new JSObject();
            response.put("cancelled", true);
            call.resolve(response);
            return;
        }
        // Document providers (including cloud storage) can block: never write on the UI thread.
        getBridge().execute(() -> {
            try (OutputStream stream = getContext().getContentResolver()
                    .openOutputStream(result.getData().getData(), "wt")) {
                if (stream == null) throw new java.io.IOException("No writable destination.");
                byte[] bytes = Base64.decode(call.getString("base64", ""), Base64.DEFAULT);
                stream.write(bytes);
                stream.flush();
            } catch (Exception error) {
                saving = false;
                call.reject("Could not save the file to the selected location.", error);
                return;
            }
            saving = false;
            JSObject response = new JSObject();
            response.put("cancelled", false);
            call.resolve(response);
        });
    }
}
