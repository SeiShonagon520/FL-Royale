package com.firstlight.offline;

import android.app.Activity;
import android.content.res.AssetManager;
import android.util.Log;
import android.widget.Toast;

import java.io.InputStream;
import java.lang.reflect.Method;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * The narrow Android half of the stock lobby Battle button route.
 *
 * <p>The native BattlePage hook may call {@link #requestFromNative(long)} only
 * after proving that the clicked widget is the main lobby Battle button and
 * that the offline local-match route is armed. This class never calls libg or
 * opens a replay. The JNI callbacks below must only enqueue a tokenized
 * request for the native game thread to consume.</p>
 */
public final class BattlePickerRouter {
    private static final String TAG = "FirstlightBattlePicker";
    private static final long MODEL_WAIT_SECONDS = 120;
    private static final AtomicLong pendingRequest = new AtomicLong();
    private static final ExecutorService preloadWaiter = Executors.newSingleThreadExecutor(task -> {
        Thread thread = new Thread(task, "firstlight-battle-picker");
        thread.setDaemon(true);
        return thread;
    });

    private BattlePickerRouter() {}

    /**
     * Called from the verified main BattlePage click hook. Returning true means
     * this click is owned by the picker; the caller may then skip the stock
     * network battle handler. No Android view is touched on the caller thread.
     */
    public static boolean requestFromNative(long requestId) {
        if (requestId <= 0) return false;
        Activity activity = currentGameActivity();
        if (!isUsable(activity)) return false;
        if (!pendingRequest.compareAndSet(0, requestId)) {
            // A second Battle tap while a dialog or model load is active is
            // already covered by the first request.
            return true;
        }
        try {
            activity.runOnUiThread(() -> showOnUiThread(activity, requestId));
            return true;
        } catch (RuntimeException failure) {
            pendingRequest.compareAndSet(requestId, 0);
            Log.e(TAG, "Could not queue opponent picker", failure);
            return false;
        }
    }

    private static Activity currentGameActivity() {
        try {
            // The pinned game exposes public static GameApp.getInstance().
            // Reflection lets the independent ORT smoke APK compile without
            // copying any original game class into the _APP sources.
            Class<?> gameApp = Class.forName("com.supercell.titan.GameApp");
            Method getter = gameApp.getMethod("getInstance");
            Object instance = getter.invoke(null);
            return instance instanceof Activity ? (Activity) instance : null;
        } catch (ReflectiveOperationException | SecurityException failure) {
            Log.e(TAG, "Stock GameApp activity is unavailable", failure);
            return null;
        }
    }

    private static boolean isUsable(Activity activity) {
        return activity != null && !activity.isFinishing() && !activity.isDestroyed();
    }

    private static void showOnUiThread(Activity activity, long requestId) {
        if (pendingRequest.get() != requestId) return;
        if (!isUsable(activity) || activity != currentGameActivity()) {
            cancel(requestId);
            return;
        }
        try {
            AiOpponentPicker.Catalog catalog = AiOpponentPicker.readCatalog(activity.getAssets());
            Set<String> available = availableModels(activity.getAssets(), catalog);
            if (available.isEmpty()) {
                Toast.makeText(activity, "未找到可用的本地 AI 模型", Toast.LENGTH_SHORT).show();
                cancel(requestId);
                return;
            }
            // Mode first, then opponent. GameModePicker was a fully built but
            // never-called class, so the mode a player picks -- including all
            // three PvE schedules -- was unreachable no matter what it listed.
            // It has to sit in front of the AI list here, in the one place the
            // lobby opens a chooser, or the modes are dead code.
            // A scripted mode has no opponent to pick: its waves come from the schedule,
            // so the opponent list would be asking the player to choose something
            // the match never uses. Start the battle with the model that is
            // already loaded, which is the policy that plays the player's side.
            GameModePicker.show(activity, () -> {
                if (GameModePicker.isScripted(GameModePicker.selectedMode(activity).id)) {
                    // A roguelike climb is the one mode with a genuine solo/team
                    // choice, so the ally toggle is asked here -- after the mode is
                    // known to be a climb, and before the model is loaded, so the
                    // answer is already in the deck payload by the time the match
                    // is built. Cancelling this dialog aborts the whole request
                    // rather than silently starting a solo run the player did not
                    // ask for.
                    GameModePicker.askAiAlly(activity, ally -> {
                        String id = selectedModelId(activity, catalog);
                        if (id != null) {
                            preload(activity, catalog, requestId, id);
                        } else {
                            Toast.makeText(activity, "没有可用的本地模型，无法开始单人挑战",
                                    Toast.LENGTH_SHORT).show();
                            cancel(requestId);
                        }
                    });
                    return;
                }
                AiOpponentPicker.showForBattleWithDuel(activity, catalog, available,
                        id -> preload(activity, catalog, requestId, id),
                        (seat0, seat1) -> prepareDuel(activity, catalog, requestId, seat0, seat1),
                        () -> cancel(requestId));
            }, () -> cancel(requestId));
        } catch (Exception failure) {
            Log.e(TAG, "Could not show opponent picker", failure);
            Toast.makeText(activity, "无法打开 AI 选择", Toast.LENGTH_SHORT).show();
            cancel(requestId);
        }
    }

    /**
     * The model a scripted mode should run under.
     *
     * A wave-driven mode still needs a policy to play the player's own side, so
     * it reuses whatever the player last chose rather than asking again. The
     * catalogue default is the fallback, and null only when the catalogue itself
     * is empty.
     */
    private static String selectedModelId(Activity activity, AiOpponentPicker.Catalog catalog) {
        String saved = AiOpponentPicker.selectedId(activity, catalog);
        return saved != null ? saved : catalog.defaultId;
    }

    private static Set<String> availableModels(AssetManager assets, AiOpponentPicker.Catalog catalog) {
        Set<String> available = new HashSet<>();
        for (AiOpponentPicker.Choice choice : catalog.choices) {
            try {
                OnDeviceOrtPolicy.ModelSpec spec = PolicyModelCatalog.forId(choice.id);
                try (InputStream ignored = assets.open(spec.modelAsset)) {
                    available.add(choice.id);
                }
            } catch (Exception missing) {
                Log.i(TAG, "Opponent asset unavailable: " + choice.id);
            }
        }
        return available;
    }

    private static void preload(Activity activity, AiOpponentPicker.Catalog catalog,
                                long requestId, String id) {
        if (pendingRequest.get() != requestId) return;
        final Future<Long> ready;
        try {
            ready = GameOrtBootstrap.setModel(activity, id, PolicyModelCatalog.forId(id));
        } catch (RuntimeException failure) {
            Log.e(TAG, "Could not queue AI model", failure);
            showFailureAndCancel(activity, requestId);
            return;
        }
        try {
            preloadWaiter.execute(() -> {
                try {
                    ready.get(MODEL_WAIT_SECONDS, TimeUnit.SECONDS);
                    if (pendingRequest.get() != requestId) return;
                    GameOrtBootstrap.PolicyLease lease = GameOrtBootstrap.acquire();
                    if (lease.policy == null || !id.equals(lease.aiId)
                            || !GameOrtBootstrap.isCurrent(lease)) {
                        throw new IllegalStateException("Selected AI policy changed before battle handoff");
                    }
                    activity.runOnUiThread(() -> finishReady(activity, catalog, requestId, id, lease));
                } catch (InterruptedException failure) {
                    Thread.currentThread().interrupt();
                    Log.e(TAG, "AI model wait interrupted", failure);
                    showFailureAndCancel(activity, requestId);
                } catch (Exception failure) {
                    Log.e(TAG, "AI model is not ready", failure);
                    showFailureAndCancel(activity, requestId);
                }
            });
        } catch (RuntimeException failure) {
            Log.e(TAG, "Could not queue AI model wait", failure);
            showFailureAndCancel(activity, requestId);
        }
    }

    private static void finishReady(Activity activity, AiOpponentPicker.Catalog catalog,
                                    long requestId, String id,
                                    GameOrtBootstrap.PolicyLease lease) {
        if (pendingRequest.get() != requestId) return;
        if (!isUsable(activity) || activity != currentGameActivity()
                || !GameOrtBootstrap.isCurrent(lease)) {
            cancel(requestId);
            return;
        }
        if (!AiOpponentPicker.rememberSelectedId(activity, catalog, id)) {
            showFailureAndCancel(activity, requestId);
            return;
        }
        try {
            // The native implementation only stores this request in a mailbox.
            // libg scene setup must run later on its own game thread.
            nativeOnBattleReady(requestId, id, lease.generation);
            pendingRequest.compareAndSet(requestId, 0);
        } catch (UnsatisfiedLinkError failure) {
            Log.e(TAG, "Native battle callback is missing", failure);
            showFailureAndCancel(activity, requestId);
        }
    }

    /**
     * Duel handoff. Both models are opened through the duel registry (the
     * bootstrap holds a single slot), then the scene starts exactly like a
     * normal battle.
     */
    private static void prepareDuel(Activity activity, AiOpponentPicker.Catalog catalog,
                                    long requestId, String seat0, String seat1) {
        if (pendingRequest.get() != requestId) return;
        try {
            preloadWaiter.execute(() -> {
                try {
                    for (String aiId : new String[] {seat0, seat1}) {
                        DuelPolicyRegistry.prepare(activity, aiId,
                                PolicyModelCatalog.forId(aiId)).get(MODEL_WAIT_SECONDS, TimeUnit.SECONDS);
                    }
                    if (pendingRequest.get() != requestId) {
                        DuelPolicyRegistry.closeAll();
                        return;
                    }
                    activity.runOnUiThread(() -> finishDuelReady(activity, requestId, seat0));
                } catch (InterruptedException failure) {
                    Thread.currentThread().interrupt();
                    DuelPolicyRegistry.closeAll();
                    showDuelFailureAndFallback(activity, catalog, requestId);
                } catch (Exception failure) {
                    Log.e(TAG, "Duel models are not ready", failure);
                    DuelPolicyRegistry.closeAll();
                    showDuelFailureAndFallback(activity, catalog, requestId);
                }
            });
        } catch (RuntimeException failure) {
            Log.e(TAG, "Could not queue duel model wait", failure);
            DuelPolicyRegistry.closeAll();
            showDuelFailureAndFallback(activity, catalog, requestId);
        }
    }

    /**
     * The native bridge drops a ready request whose aiId is not one of the five
     * catalog models or whose generation is not positive
     * ({@code battle_picker_bridge.cpp}). Passing anything else left the native
     * Battle button stuck for the whole process, so the seat-0 model id and a
     * registry generation are used here. The duel pipeline reads both seats from
     * the fixture, so neither value changes what is actually played.
     */
    private static void finishDuelReady(Activity activity, long requestId, String seat0) {
        if (pendingRequest.get() != requestId) return;
        if (!isUsable(activity) || activity != currentGameActivity()) {
            DuelPolicyRegistry.closeAll();
            cancel(requestId);
            return;
        }
        try {
            long generation = DuelPolicyRegistry.nextGeneration();
            if (seat0 == null || seat0.isEmpty() || generation <= 0) {
                throw new IllegalStateException("Duel handoff token is invalid");
            }
            // The native bridge only records the mailbox entry; libg scene setup
            // runs later on its own game thread and calls back into
            // prepareMatch. Flag that callback as a duel before handing off.
            LocalBattleRuntime.beginDuel();
            nativeOnBattleReady(requestId, seat0, generation);
            pendingRequest.compareAndSet(requestId, 0);
        } catch (UnsatisfiedLinkError failure) {
            Log.e(TAG, "Native battle callback is missing", failure);
            DuelPolicyRegistry.closeAll();
            showFailureAndCancel(activity, requestId);
        } catch (RuntimeException failure) {
            Log.e(TAG, "Duel handoff rejected", failure);
            DuelPolicyRegistry.closeAll();
            showFailureAndCancel(activity, requestId);
        }
    }

    /**
     * A duel failure must never strand the player: release the native request
     * and notify the player so a fresh battle can be started cleanly.
     */
    private static void showDuelFailureAndFallback(Activity activity,
                                                   AiOpponentPicker.Catalog catalog,
                                                   long requestId) {
        if (!isUsable(activity)) {
            cancel(requestId);
            return;
        }
        activity.runOnUiThread(() -> {
            if (pendingRequest.get() == requestId && isUsable(activity)) {
                Toast.makeText(activity, "AI 自战模型加载失败，请重试", Toast.LENGTH_SHORT).show();
            }
            cancel(requestId);
            // Cancel releases native request. Avoid requestFromNative with forged id.
        });
    }

    /** Ask native for a fresh battle request */

    private static void showFailureAndCancel(Activity activity, long requestId) {
        if (isUsable(activity)) {
            activity.runOnUiThread(() -> {
                if (pendingRequest.get() == requestId && isUsable(activity)) {
                    Toast.makeText(activity, "AI 模型加载失败", Toast.LENGTH_SHORT).show();
                }
                cancel(requestId);
            });
        } else {
            cancel(requestId);
        }
    }

    private static void cancel(long requestId) {
        if (!pendingRequest.compareAndSet(requestId, 0)) return;
        try {
            nativeOnBattleCancelled(requestId);
        } catch (UnsatisfiedLinkError failure) {
            Log.e(TAG, "Native battle cancel callback is missing", failure);
        }
    }

    private static native void nativeOnBattleReady(long requestId, String aiId, long modelGeneration);
    private static native void nativeOnBattleCancelled(long requestId);
}
