package com.firstlight.offline;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.os.Bundle;
import android.util.Log;
import android.view.Gravity;
import android.widget.TextView;

/** Launcher gate: prepare resources off the UI thread, then open the native game. */
public final class ResourceBootstrapActivity extends Activity {
    private static final String TAG = "FirstlightResource";
    private TextView messageView;

    @Override
    public void onCreate(Bundle state) {
        super.onCreate(state);
        messageView = new TextView(this);
        messageView.setGravity(Gravity.CENTER);
        messageView.setTextSize(18);
        messageView.setPadding(32, 32, 32, 32);
        setContentView(messageView);
        startResourceSetup();
    }

    private void startResourceSetup() {
        if (isFinishing() || isDestroyed()) return;
        messageView.setText("正在准备离线游戏资源...");
        new Thread(() -> {
            try {
                long started = System.currentTimeMillis();
                String result = RuntimeUpdateInstaller.ensureInstalled(this, (done, total) -> {
                    if (done == total || done % 50 == 0) {
                        runOnUiThread(() -> {
                            if (!isFinishing() && !isDestroyed()) {
                                messageView.setText("正在准备离线资源: " + done + " / " + total);
                            }
                        });
                    }
                });
                Log.i(TAG, "game resources=" + result + " elapsed_ms="
                        + (System.currentTimeMillis() - started));
                runOnUiThread(() -> {
                    if (isFinishing() || isDestroyed()) return;
                    Intent game = new Intent();
                    game.setClassName(getPackageName(), "com.supercell.clashroyale.GameApp");
                    startActivity(game);
                    finish();
                });
            } catch (Exception error) {
                Log.e(TAG, "Game resource setup failed", error);
                runOnUiThread(() -> {
                    if (isFinishing() || isDestroyed()) return;
                    messageView.setText("离线资源准备失败：\n" + error.getMessage());
                    new AlertDialog.Builder(this, android.R.style.Theme_Material_Light_Dialog_Alert)
                        .setTitle("离线资源准备失败")
                        .setMessage("无法解压或验证离线资源：\n" + error.getMessage() + "\n\n请检查存储空间后重试。")
                        .setCancelable(false)
                        .setPositiveButton("重试", (dialog, which) -> startResourceSetup())
                        .setNegativeButton("退出", (dialog, which) -> finish())
                        .show();
                });
            }
        }, "firstlight-resource-setup").start();
    }
}
