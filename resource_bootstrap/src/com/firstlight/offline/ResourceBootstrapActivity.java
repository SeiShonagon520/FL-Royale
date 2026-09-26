package com.firstlight.offline;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.util.Log;
import android.view.Gravity;
import android.widget.TextView;

/** Launcher gate: prepare resources off the UI thread, then open the native game. */
public final class ResourceBootstrapActivity extends Activity {
    private static final String TAG = "FirstlightResource";

    @Override
    public void onCreate(Bundle state) {
        super.onCreate(state);
        TextView message = new TextView(this);
        message.setGravity(Gravity.CENTER);
        message.setTextSize(20);
        message.setText("Preparing offline game resources...");
        setContentView(message);
        new Thread(() -> {
            try {
                long started = System.currentTimeMillis();
                String result = RuntimeUpdateInstaller.ensureInstalled(this, (done, total) -> {
                    if (done == total || done % 50 == 0) {
                        runOnUiThread(() -> message.setText("Preparing offline resources: "
                                + done + "/" + total));
                    }
                });
                Log.i(TAG, "game resources=" + result + " elapsed_ms="
                        + (System.currentTimeMillis() - started));
                runOnUiThread(() -> {
                    Intent game = new Intent();
                    game.setClassName(getPackageName(), "com.supercell.clashroyale.GameApp");
                    startActivity(game);
                    finish();
                });
            } catch (Exception error) {
                Log.e(TAG, "Game resource setup failed", error);
                runOnUiThread(() -> message.setText("Could not prepare offline resources:\n"
                        + error.getMessage()));
            }
        }, "firstlight-resource-setup").start();
    }
}
