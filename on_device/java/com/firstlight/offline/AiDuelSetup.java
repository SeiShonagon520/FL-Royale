package com.firstlight.offline;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.LinearLayout;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Single-screen seat configuration for AI-vs-AI.
 *
 * <p>Both seats are chosen with spinners on one dialog, so there is no stack of
 * nested pickers. "Back" returns to the opponent list supplied by
 * {@code onBack}, and every dismiss path routes through the same callback, so
 * the native battle request can never be left waiting.</p>
 *
 * <p>Seat 0 keeps whatever deck is equipped in Collection &rarr; Decks, so only
 * the seat-1 AI needs a separately saved deck. The two spinners are independent:
 * naming the same model on both seats is true self-play, and the registry then
 * shares a single policy instance between them.</p>
 */
public final class AiDuelSetup {
    private static final String PREFS = "firstlight_offline_ai_duel";
    private static final String SEAT0_MODEL = "seat0_model";
    private static final String SEAT1_MODEL = "seat1_model";

    private AiDuelSetup() {}

    public static String[] selectedAiIds(Context context) throws Exception {
        AiOpponentPicker.Catalog catalog =
                AiOpponentPicker.readCatalog(context.getApplicationContext().getAssets());
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        return new String[] {
            known(catalog, prefs.getString(SEAT0_MODEL, null), catalog.defaultId),
            known(catalog, prefs.getString(SEAT1_MODEL, null), catalog.defaultId)
        };
    }

    public static String labelFor(Context context, String aiId) {
        try {
            for (AiOpponentPicker.Choice choice
                    : AiOpponentPicker.readCatalog(context.getApplicationContext().getAssets()).choices)
                if (choice.id.equals(aiId)) return choice.label;
        } catch (Exception ignored) { }
        return aiId;
    }

    private static String known(AiOpponentPicker.Catalog catalog, String id, String fallback) {
        if (id == null) return fallback;
        for (AiOpponentPicker.Choice choice : catalog.choices)
            if (choice.id.equals(id)) return id;
        return fallback;
    }

    /**
     * Show the one-screen duel setup.
     *
     * @param onStart   invoked with the two chosen ids once both models are live
     * @param onBack    invoked on "返回" and on any dismiss, so the caller can
     *                  return to the opponent list or cancel the native request
     */
    public static void show(Activity activity, OnStart onStart, Runnable onBack) {
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) {
            if (onBack != null) onBack.run();
            return;
        }
        final Context context = activity;
        final AiOpponentPicker.Catalog catalog;
        try {
            catalog = AiOpponentPicker.readCatalog(activity.getAssets());
        } catch (Exception error) {
            Toast.makeText(activity, "无法读取 AI 列表", Toast.LENGTH_SHORT).show();
            if (onBack != null) onBack.run();
            return;
        }
        final List<AiOpponentPicker.Choice> choices = catalog.choices;
        if (choices.isEmpty()) {
            Toast.makeText(activity, "没有可用的 AI 模型", Toast.LENGTH_SHORT).show();
            if (onBack != null) onBack.run();
            return;
        }
        final Set<String> available = new HashSet<>();
        for (AiOpponentPicker.Choice choice : choices) available.add(choice.id);

        final String[] seats;
        try {
            seats = selectedAiIds(context);
        } catch (Exception error) {
            Toast.makeText(activity, "无法读取对战配置", Toast.LENGTH_SHORT).show();
            if (onBack != null) onBack.run();
            return;
        }

        final int density = Math.max(1, (int) activity.getResources().getDisplayMetrics().density);
        final int pad = 20 * density;
        LinearLayout panel = new LinearLayout(activity);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(pad, pad / 2, pad, 0);

        final Spinner seat0 = buildSpinner(activity, choices, seats[0], available, density);
        final Spinner seat1 = buildSpinner(activity, choices, seats[1], available, density);

        panel.addView(label(activity, "上方 AI（使用你的当前卡组）", density));
        panel.addView(seat0);
        panel.addView(spacer(activity, 8 * density));
        panel.addView(label(activity, "下方 AI（使用已保存的 AI 卡组）", density));
        panel.addView(seat1);
        panel.addView(spacer(activity, 12 * density));
        panel.addView(hint(activity,
                "两个模型可以选同一个，那是真正的自博弈。\n"
                        + "对局中无法操作，只能观战。", density));
        panel.addView(spacer(activity, 4 * density));

        TextView edit = new TextView(activity);
        edit.setText("保存下方 AI 卡组…");
        edit.setTextColor(0xff0066cc);
        edit.setTextSize(15);
        edit.setPadding(0, 8 * density, 0, 8 * density);
        edit.setOnClickListener(v -> {
            try {
                AiDeckSettings.show(activity, catalog);
            } catch (Exception error) {
                Toast.makeText(activity, "无法打开卡组配置", Toast.LENGTH_SHORT).show();
            }
        });
        panel.addView(edit);

        final AlertDialog dialog = new AlertDialog.Builder(activity,
                android.R.style.Theme_Material_Light_Dialog_Alert)
                .setTitle("AI 自战演练")
                .setView(panel)
                .setPositiveButton("开始观战", null)
                .setNegativeButton("返回", (d, which) -> { })
                .create();

        final boolean[] starting = {false};
        dialog.setOnCancelListener(d -> { if (!starting[0] && onBack != null) onBack.run(); });
        dialog.setOnDismissListener(d -> { if (!starting[0] && onBack != null) onBack.run(); });
        dialog.show();
        // The positive button is wired after show() so the spinners are read at
        // the moment of the tap rather than when the dialog was built.
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            AiOpponentPicker.Choice top = choices.get(seat0.getSelectedItemPosition());
            AiOpponentPicker.Choice bottom = choices.get(seat1.getSelectedItemPosition());
            if (!available.contains(top.id) || !available.contains(bottom.id)) {
                Toast.makeText(activity, "请选择已安装的模型", Toast.LENGTH_SHORT).show();
                return;
            }
            remember(context, 0, top.id);
            remember(context, 1, bottom.id);
            starting[0] = true;
            dialog.dismiss();
            Toast.makeText(activity,
                    top.label + "  VS  " + bottom.label, Toast.LENGTH_SHORT).show();
            if (onStart != null) onStart.onStart(top.id, bottom.id);
        });
    }

    private static Spinner buildSpinner(Activity activity,
                                         List<AiOpponentPicker.Choice> choices,
                                         String selected, Set<String> available, int density) {
        String[] labels = new String[choices.size()];
        for (int i = 0; i < labels.length; i++) {
            AiOpponentPicker.Choice choice = choices.get(i);
            labels[i] = choice.label + (available.contains(choice.id) ? "" : "（未安装）");
        }
        ArrayAdapter<String> adapter = new ArrayAdapter<String>(activity,
                android.R.layout.simple_spinner_dropdown_item, labels) {
            @Override public boolean isEnabled(int position) {
                return available.contains(choices.get(position).id);
            }
            @Override public View getView(int position, View convertView, ViewGroup parent) {
                TextView view = (TextView) super.getView(position, convertView, parent);
                view.setEnabled(isEnabled(position));
                view.setAlpha(isEnabled(position) ? 1.0f : 0.45f);
                view.setTextSize(16);
                return view;
            }
        };
        Spinner spinner = new Spinner(activity);
        spinner.setAdapter(adapter);
        for (int i = 0; i < choices.size(); i++)
            if (choices.get(i).id.equals(selected)) {
                spinner.setSelection(i);
                break;
            }
        return spinner;
    }

    private static TextView label(Activity activity, String text, int density) {
        TextView view = new TextView(activity);
        view.setText(text);
        view.setTextSize(14);
        view.setTextColor(0xff555555);
        view.setPadding(0, 0, 0, 0);
        return view;
    }

    private static TextView hint(Activity activity, String text, int density) {
        TextView view = new TextView(activity);
        view.setText(text);
        view.setTextSize(13);
        view.setTextColor(0xff777777);
        view.setGravity(Gravity.START);
        return view;
    }

    private static View spacer(Activity activity, int height) {
        View view = new View(activity);
        view.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, height));
        return view;
    }

    private static void remember(Context context, int seat, String aiId) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(seat == 0 ? SEAT0_MODEL : SEAT1_MODEL, aiId).commit();
    }

    /** Callback carrying the two selected model ids. */
    public interface OnStart {
        void onStart(String seat0AiId, String seat1AiId);
    }
}
