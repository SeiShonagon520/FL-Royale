package com.firstlight.localruntime;
import android.app.Activity;
import android.os.Bundle;
import android.widget.TextView;
import android.util.Log;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

public final class MainActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        TextView view = new TextView(this);
        view.setText("Checking on-device FAIR semantics…");
        setContentView(view);
        new Thread(() -> {
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                String result = Python.getInstance().getModule("mobile_fair").callAttr("smoke").toString();
                java.io.File frames = new java.io.File(getFilesDir(), "frames");
                if (frames.isDirectory()) result = Python.getInstance().getModule("mobile_fair").callAttr("verify_device_frames",frames.toString()).toString();
                Log.i("FirstlightSemantics", result);
                final String displayed = result;
                runOnUiThread(() -> view.setText(displayed));
            } catch (Throwable error) {
                Log.e("FirstlightSemantics", "Smoke failed", error);
                runOnUiThread(() -> view.setText(error.toString()));
            }
        }, "firstlight-fair-smoke").start();
    }
}
