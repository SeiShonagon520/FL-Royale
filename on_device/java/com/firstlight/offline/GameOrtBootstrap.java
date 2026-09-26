package com.firstlight.offline;

import android.content.Context;
import android.util.Log;

import java.util.Objects;
import java.util.concurrent.CancellationException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.FutureTask;

/**
 * Game-process entry point for the local policy worker.
 *
 * <p>The launcher calls {@link #start(Context)} during Activity creation. A
 * lobby model picker may call {@link #setModel(Context, String,
 * OnDeviceOrtPolicy.ModelSpec)} when no battle is active. A battle task should
 * retain a {@link PolicyLease} and check {@link #isCurrent(PolicyLease)} before
 * applying any asynchronous result.</p>
 */
public final class GameOrtBootstrap {
    private static final String TAG = "FirstlightOrtBootstrap";
    private static OnDeviceOrtPolicy policy;
    private static String selectedId;
    private static String currentModelSha256;
    private static long generation;
    private static long selectionRequest;
    private static String pendingId;
    private static String pendingSha256;
    private static Future<Long> pendingSwitch;
    private static final ExecutorService SWITCH_EXECUTOR = Executors.newSingleThreadExecutor(command -> {
        Thread thread = new Thread(command, "firstlight-ai-switch");
        thread.setDaemon(true);
        return thread;
    });

    private GameOrtBootstrap() {}

    public static final class PolicyLease {
        public final OnDeviceOrtPolicy policy;
        public final String aiId;
        public final long generation;

        private PolicyLease(OnDeviceOrtPolicy policy, String aiId, long generation) {
            this.policy = policy;
            this.aiId = aiId;
            this.generation = generation;
        }
    }

    /** Queue General model load without waiting on the game/UI thread. */
    public static synchronized void start(Context context) {
        if (policy != null || pendingSwitch != null) return;
        try {
            setModel(context, "general", OnDeviceOrtPolicy.ModelSpec.general());
        } catch (Throwable failure) {
            Log.e(TAG, "Policy preload could not be queued", failure);
        }
    }

    /**
     * Prepare a new policy off the UI thread, then publish it only after its
     * model has passed SHA verification and ORT session creation. The returned
     * Future completes after that atomic switch. A failed or superseded load
     * leaves the previous ready policy and generation intact.
     */
    public static synchronized Future<Long> setModel(
            Context context, String aiId, OnDeviceOrtPolicy.ModelSpec spec) {
        Objects.requireNonNull(context, "context");
        Objects.requireNonNull(spec, "spec");
        if (aiId == null || aiId.isEmpty()) throw new IllegalArgumentException("Missing AI id");
        if (pendingSwitch != null && aiId.equals(pendingId) &&
                spec.modelSha256.equals(pendingSha256)) {
            return pendingSwitch;
        }
        if (policy != null && aiId.equals(selectedId) &&
                spec.modelSha256.equals(currentModelSha256)) {
            // Reselecting the committed model supersedes a different pending
            // load without closing the working policy.
            selectionRequest++;
            pendingSwitch = null;
            pendingId = null;
            pendingSha256 = null;
            return policy.preload();
        }

        final Context appContext = context.getApplicationContext();
        final long request = ++selectionRequest;
        FutureTask<Long> switchTask = new FutureTask<Long>(() -> {
            OnDeviceOrtPolicy prepared = null;
            try {
                prepared = new OnDeviceOrtPolicy(appContext, spec);
                // This wait runs only on firstlight-ai-switch. The candidate's
                // private AI worker performs the model load and native ORT call.
                long loadNanos = prepared.preload().get();
                OnDeviceOrtPolicy previous;
                long committedGeneration;
                synchronized (GameOrtBootstrap.class) {
                    if (request != selectionRequest) {
                        throw new CancellationException("AI model selection was superseded");
                    }
                    previous = policy;
                    policy = prepared;
                    prepared = null;
                    selectedId = aiId;
                    currentModelSha256 = spec.modelSha256;
                    committedGeneration = ++generation;
                    pendingSwitch = null;
                    pendingId = null;
                    pendingSha256 = null;
                }
                if (previous != null) previous.close();
                Log.i(TAG, "Policy committed ai=" + aiId + " generation=" +
                        committedGeneration + " sha256=" + spec.modelSha256);
                return loadNanos;
            } catch (CancellationException superseded) {
                Log.i(TAG, "Policy switch superseded ai=" + aiId + " request=" + request);
                throw superseded;
            } catch (Exception | Error failure) {
                Log.e(TAG, "Policy switch failed ai=" + aiId, failure);
                throw failure;
            } finally {
                if (prepared != null) prepared.close();
                synchronized (GameOrtBootstrap.class) {
                    if (request == selectionRequest) {
                        pendingSwitch = null;
                        pendingId = null;
                        pendingSha256 = null;
                    }
                }
            }
        }) {
            @Override
            public boolean cancel(boolean mayInterruptIfRunning) {
                // Supersession via setModel() is the supported cancellation.
                // Do not report a canceled Future after a policy was committed.
                return false;
            }
        };
        pendingSwitch = switchTask;
        pendingId = aiId;
        pendingSha256 = spec.modelSha256;
        try {
            SWITCH_EXECUTOR.execute(switchTask);
        } catch (RuntimeException | Error failure) {
            pendingSwitch = null;
            pendingId = null;
            pendingSha256 = null;
            throw failure;
        }
        Log.i(TAG, "Policy preload queued ai=" + aiId + " request=" + request +
                " sha256=" + spec.modelSha256);
        return switchTask;
    }

    public static synchronized PolicyLease acquire() {
        return new PolicyLease(policy, selectedId, generation);
    }

    public static synchronized boolean isCurrent(PolicyLease lease) {
        return lease != null && lease.policy != null && lease.policy == policy &&
                lease.generation == generation;
    }

    public static synchronized OnDeviceOrtPolicy getPolicy() {
        return policy;
    }
}
