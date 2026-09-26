package com.firstlight.offline;

import android.app.Activity;
import android.os.Bundle;
import android.util.Log;
import android.view.Gravity;
import android.widget.TextView;

/** Separate-package harness. It never reads or writes the Clash Royale app's data. */
public final class ResourceProbeActivity extends Activity {
    private static final String TAG = "FirstlightResource";
    private TextView message;

    @Override
    public void onCreate(Bundle state) {
        super.onCreate(state);
        message = new TextView(this);
        message.setGravity(Gravity.CENTER);
        message.setTextSize(20);
        message.setText("Checking bundled offline resources...");
        setContentView(message);
        new Thread(() -> {
            try {
                long started = System.currentTimeMillis();
                String result = RuntimeUpdateInstaller.ensureInstalled(this, (done, total) -> {
                    if (done == total || done % 50 == 0) {
                        runOnUiThread(() -> message.setText("Offline resources: " + done + "/" + total));
                    }
                });
                long elapsed = System.currentTimeMillis() - started;
                Log.i(TAG, "result=" + result + " elapsed_ms=" + elapsed);
                runOnUiThread(() -> message.setText("Offline resources " + result + "\n" + elapsed + " ms"));
            } catch (Exception error) {
                Log.e(TAG, "resource setup failed", error);
                runOnUiThread(() -> message.setText("Resource setup failed: " + error.getMessage()));
            }
        }, "firstlight-resource-setup").start();
    }
}
