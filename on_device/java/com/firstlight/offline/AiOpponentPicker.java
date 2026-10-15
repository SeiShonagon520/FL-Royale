package com.firstlight.offline;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.res.AssetManager;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;

/**
 * The Android side of the lobby's opponent choice. The caller is responsible
 * for opening the stock battle scene after {@link SelectionListener#onSelected}.
 * A model is selectable only when its verified runtime asset is available.
 */
public final class AiOpponentPicker {
    public static final String CATALOG_ASSET = "firstlight/ai_catalog.json";
    private static final String SCHEMA = "firstlight-android-ai-catalog.v1";
    private static final String PREFS = "firstlight_offline_ai";
    private static final String SELECTED_ID = "selected_opponent_id";
    private static final int MAX_CATALOG_BYTES = 16 * 1024;

    public interface SelectionListener {
        void onSelected(String id);
    }

    public static final class Choice {
        public final String id;
        public final String label;
        public final String description;

        private Choice(String id, String label, String description) {
            this.id = id;
            this.label = label;
            this.description = description;
        }
    }

    public static final class Catalog {
        public final String defaultId;
        public final List<Choice> choices;

        private Catalog(String defaultId, List<Choice> choices) {
            this.defaultId = defaultId;
            this.choices = Collections.unmodifiableList(choices);
        }
    }

    private AiOpponentPicker() {}

    public static Catalog readCatalog(AssetManager assets) throws Exception {
        byte[] data;
        try (InputStream input = assets.open(CATALOG_ASSET);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int count;
            while ((count = input.read(buffer)) != -1) {
                if (output.size() + count > MAX_CATALOG_BYTES) {
                    throw new IOException("AI catalog is too large");
                }
                output.write(buffer, 0, count);
            }
            data = output.toByteArray();
        }
        JSONObject root = new JSONObject(new String(data, StandardCharsets.UTF_8));
        if (!SCHEMA.equals(root.getString("schema"))) {
            throw new IllegalArgumentException("Unsupported AI catalog schema");
        }
        JSONArray models = root.getJSONArray("models");
        if (models.length() < 1 || models.length() > 16) {
            throw new IllegalArgumentException("Invalid AI catalog size");
        }
        List<Choice> choices = new ArrayList<>(models.length());
        Set<String> ids = new HashSet<>();
        for (int i = 0; i < models.length(); i++) {
            JSONObject model = models.getJSONObject(i);
            String id = model.getString("id");
            String label = model.getString("label");
            String description = model.optString("description", "");
            if (!id.matches("[a-z0-9_]{1,48}") || label.isEmpty()
                    || label.length() > 96 || description.length() > 96 || !ids.add(id)) {
                throw new IllegalArgumentException("Invalid AI catalog entry " + i);
            }
            choices.add(new Choice(id, label, description));
        }
        String defaultId = root.getString("default");
        if (!ids.contains(defaultId)) {
            throw new IllegalArgumentException("AI catalog default is missing");
        }
        return new Catalog(defaultId, choices);
    }

    public static String selectedId(Context context, Catalog catalog) {
        String saved = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getString(SELECTED_ID, catalog.defaultId);
        for (Choice choice : catalog.choices) {
            if (choice.id.equals(saved)) {
                return saved;
            }
        }
        return catalog.defaultId;
    }

    /** Save only a catalog entry that has successfully loaded for a battle. */
    public static boolean rememberSelectedId(Context context, Catalog catalog, String id) {
        Objects.requireNonNull(context, "context");
        Objects.requireNonNull(catalog, "catalog");
        for (Choice choice : catalog.choices) {
            if (choice.id.equals(id)) {
                return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                        .edit().putString(SELECTED_ID, id).commit();
            }
        }
        return false;
    }

    /** Call from the UI thread with the IDs of models actually present in the APK. */
    public static void show(Activity activity, Catalog catalog, Set<String> availableIds,
                            SelectionListener listener) {
        showInternal(activity, catalog, availableIds, listener, null, true);
    }

    /**
     * Show the native-lobby battle picker. The caller commits the choice only
     * after its model has been verified and loaded. Cancellation releases the
     * matching native battle request without changing the saved AI.
     */
    public static void showForBattle(Activity activity, Catalog catalog, Set<String> availableIds,
                                     SelectionListener listener, Runnable onCancelled) {
        showInternal(activity, catalog, availableIds, listener, onCancelled, false);
    }

    /**
     * Battle picker with an extra "AI 自战（观战）" row above the model list.
     *
     * <p>AI-vs-AI is a way to pick an opponent, not a game mode: putting it here
     * keeps the whole flow at one tap and leaves the normal ladder/duel flow
     * byte-for-byte unchanged. Picking the duel row hands control to
     * {@link AiDuelSetup}; the dialog is rebuilt if the player backs out, so
     * there is no dead end.</p>
     */
    public static void showForBattleWithDuel(Activity activity, Catalog catalog, Set<String> availableIds,
                                              SelectionListener listener,
                                              AiDuelSetup.OnStart onDuelStart, Runnable onCancelled) {
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) {
            if (onCancelled != null) onCancelled.run();
            return;
        }
        final GameModePicker.Mode currentMode = GameModePicker.selectedMode(activity);
        // Row 0 is the AI duel, row 1 is the current game mode, then the models.
        // The mode used to live only in a neutral button, which is easy to miss.
        final int duelRow = 0, modeRow = 1, modelBase = 2;
        final String[] labels = new String[catalog.choices.size() + modelBase];
        labels[duelRow] = "🤖 AI 自战（观战）";
        labels[modeRow] = "🎮 玩法：" + currentMode.label;
        for (int i = 0; i < catalog.choices.size(); i++) {
            Choice choice = catalog.choices.get(i);
            labels[modelBase + i] = choice.label + (availableIds.contains(choice.id) ? "" : "（未安装）");
        }
        ArrayAdapter<String> adapter = new ArrayAdapter<String>(activity,
                android.R.layout.simple_list_item_2, android.R.id.text1, labels) {
            @Override public boolean isEnabled(int position) {
                if (position < modelBase) return true;
                return availableIds.contains(catalog.choices.get(position - modelBase).id);
            }
            @Override public View getView(int position, View convertView, ViewGroup parent) {
                View view = super.getView(position, convertView, parent);
                android.widget.TextView title = view.findViewById(android.R.id.text1);
                android.widget.TextView subtitle = view.findViewById(android.R.id.text2);
                int color = 0xff161616;
                if (position < modelBase) color = 0xff0066cc;
                if (title != null) {
                    title.setText(labels[position]);
                    title.setTextColor(color);
                    title.setTextSize(16);
                }
                if (subtitle != null) {
                    String text;
                    if (position == duelRow) {
                        text = "两个 AI 互相攻击，可选不同模型或同一模型自博弈";
                    } else if (position == modeRow) {
                        text = "【" + currentMode.category + "】" + currentMode.description
                                + (currentMode.policyNote != null ? "  ✓ 已验证" : "");
                    } else {
                        text = catalog.choices.get(position - modelBase).description;
                    }
                    subtitle.setText(text);
                    subtitle.setTextColor(0xff666666);
                    subtitle.setTextSize(13);
                }
                view.setAlpha(isEnabled(position) ? 1.0f : 0.45f);
                return view;
            }
        };
        // Set by any path that hands control elsewhere (model load or the duel
        // screen) so the resulting dismissal is not treated as a cancellation.
        final java.util.concurrent.atomic.AtomicBoolean suppressCancel =
                new java.util.concurrent.atomic.AtomicBoolean();
        final AlertDialog dialog = new AlertDialog.Builder(activity,
                android.R.style.Theme_Material_Light_Dialog_Alert)
                .setTitle("选择对战方式")
                .setAdapter(adapter, (d, which) -> {
                    if (which == duelRow) {
                        // The duel screen owns its own dismissal; reopening this
                        // list is its "返回" action.
                        suppressCancel.set(true);
                        d.dismiss();
                        AiDuelSetup.show(activity, onDuelStart,
                                () -> showForBattleWithDuel(activity, catalog, availableIds,
                                        listener, onDuelStart, onCancelled));
                        return;
                    }
                    if (which == modeRow) {
                        // Swap the list in place: picking a mode returns to this
                        // same screen with the new mode shown, so the player
                        // never has to hunt for a hidden button.
                        suppressCancel.set(true);
                        d.dismiss();
                        GameModePicker.show(activity,
                                () -> showForBattleWithDuel(activity, catalog, availableIds,
                                        listener, onDuelStart, onCancelled));
                        return;
                    }
                    Choice choice = catalog.choices.get(which - modelBase);
                    if (!availableIds.contains(choice.id)) return;
                    suppressCancel.set(true);
                    listener.onSelected(choice.id);
                })
                .setNegativeButton("取消", null)
                .create();
        if (onCancelled != null) {
            // Fires for 取消, the back key and a tap outside.
            dialog.setOnDismissListener(d -> {
                if (suppressCancel.getAndSet(false)) return;
                onCancelled.run();
            });
        }
        dialog.show();
    }

    private static void showInternal(Activity activity, Catalog catalog, Set<String> availableIds,
                                     SelectionListener listener, Runnable onCancelled,
                                     boolean rememberImmediately) {
        Objects.requireNonNull(activity, "activity");
        Objects.requireNonNull(catalog, "catalog");
        Objects.requireNonNull(availableIds, "availableIds");
        Objects.requireNonNull(listener, "listener");
        if (activity.isFinishing() || activity.isDestroyed()) {
            if (onCancelled != null) onCancelled.run();
            return;
        }
        final String[] labels = new String[catalog.choices.size()];
        for (int i = 0; i < labels.length; i++) {
            Choice choice = catalog.choices.get(i);
            labels[i] = choice.label + (availableIds.contains(choice.id) ? "" : "（未安装）");
        }
        ArrayAdapter<String> adapter = new ArrayAdapter<String>(activity,
                android.R.layout.simple_list_item_2, android.R.id.text1, labels) {
            @Override public boolean isEnabled(int position) {
                return availableIds.contains(catalog.choices.get(position).id);
            }

            @Override public View getView(int position, View convertView, ViewGroup parent) {
                View view = super.getView(position, convertView, parent);
                android.widget.TextView text = view.findViewById(android.R.id.text1);
                if (text != null) text.setTextColor(0xff161616);
                android.widget.TextView description = view.findViewById(android.R.id.text2);
                if (description != null) {
                    description.setText(catalog.choices.get(position).description);
                    description.setTextColor(0xff666666);
                    description.setTextSize(14);
                }
                view.setAlpha(isEnabled(position) ? 1.0f : 0.45f);
                return view;
            }
        };
        final boolean[] chosen = {false};
        GameModePicker.Mode currentMode = GameModePicker.selectedMode(activity);
        AlertDialog dialog = new AlertDialog.Builder(activity, android.R.style.Theme_Material_Light_Dialog_Alert)
                .setTitle("选择对战 AI  [" + currentMode.label + "]")
                .setAdapter(adapter, (ignored, which) -> {
                    Choice choice = catalog.choices.get(which);
                    if (!availableIds.contains(choice.id)) {
                        return;
                    }
                    if (rememberImmediately && !rememberSelectedId(activity, catalog, choice.id)) {
                        Toast.makeText(activity, "无法保存 AI 选择", Toast.LENGTH_SHORT).show();
                        return;
                    }
                    chosen[0] = true;
                    listener.onSelected(choice.id);
                })
                .setNeutralButton("玩法: " + currentMode.label + " (切换)", (d, which) -> {
                    chosen[0] = true;
                    GameModePicker.show(activity, () -> {
                        showInternal(activity, catalog, availableIds, listener, onCancelled, rememberImmediately);
                    });
                })
                .setNegativeButton("取消", null)
                .create();
        if (onCancelled != null) {
            dialog.setOnDismissListener(ignored -> {
                if (!chosen[0]) onCancelled.run();
            });
        }
        dialog.show();
    }
}
