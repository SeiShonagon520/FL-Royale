package com.firstlight.offline;

import android.content.Context;
import android.util.Log;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Holds one live {@link OnDeviceOrtPolicy} per model id so an AI duel can drive
 * two different models at once.
 *
 * <p>{@link GameOrtBootstrap} is a single global slot: {@code setModel} replaces
 * the committed policy and closes the previous one, so two models can never be
 * live through it. This registry is the duel-only equivalent and deliberately
 * does not touch the bootstrap: the human-vs-AI path keeps its existing
 * semantics untouched.</p>
 *
 * <p>Models are cached by id, so selecting the same model on both seats (true
 * self-play) instantiates one policy and shares the weights while each seat
 * keeps its own {@link RecurrentPolicySession}.</p>
 */
public final class DuelPolicyRegistry {
    private static final String TAG = "FirstlightDuel";

    private static final Map<String, OnDeviceOrtPolicy> POLICIES = new HashMap<>();
    private static final Map<String, String> SHA256 = new HashMap<>();
    /** Bumped on every duel handoff. The native battle-picker bridge rejects a
     *  non-positive model generation, so this must always stay positive. */
    private static long generation = 1L;
    private static final ExecutorService LOADER = Executors.newSingleThreadExecutor(command -> {
        Thread thread = new Thread(command, "firstlight-duel-model-load");
        thread.setDaemon(true);
        return thread;
    });

    private DuelPolicyRegistry() {}

    /**
     * Monotonic positive generation for the native handoff.
     *
     * <p>{@code battle_picker_bridge.cpp} drops a ready request whose generation
     * is {@code <= 0}, and that would leave the native Battle button stuck for
     * the whole process lifetime. The duel pipeline does not read the value
     * back, so it only has to be a valid, strictly positive token.</p>
     */
    public static synchronized long currentGeneration() {
        return generation;
    }

    /** Advance and return a fresh positive generation for a new duel. */
    public static synchronized long nextGeneration() {
        return ++generation;
    }

    /** Strong reference to one seat's policy, valid until {@link #closeAll()}. */
    public static final class DuelLease {
        public final OnDeviceOrtPolicy policy;
        public final String aiId;

        DuelLease(OnDeviceOrtPolicy policy, String aiId) {
            this.policy = policy;
            this.aiId = aiId;
        }
    }

    /**
     * Load (or reuse) the policy for {@code aiId} off the calling thread.
     * Completes only after the model passed its SHA check and the ORT session
     * exists, so a caller may start a duel as soon as both futures complete.
     */
    public static CompletableFuture<DuelLease> prepare(
            Context context, String aiId, OnDeviceOrtPolicy.ModelSpec spec) {
        final Context app = context.getApplicationContext();
        Objects.requireNonNull(spec, "spec");
        if (aiId == null || aiId.isEmpty()) throw new IllegalArgumentException("Missing duel AI id");
        synchronized (DuelPolicyRegistry.class) {
            OnDeviceOrtPolicy existing = POLICIES.get(aiId);
            if (existing != null && spec.modelSha256.equals(SHA256.get(aiId))) {
                return CompletableFuture.completedFuture(new DuelLease(existing, aiId));
            }
        }
        return CompletableFuture.supplyAsync(() -> {
            OnDeviceOrtPolicy prepared = new OnDeviceOrtPolicy(app, spec);
            try {
                prepared.preload().get();
            } catch (Exception | Error failure) {
                prepared.close();
                throw failure instanceof RuntimeException ? (RuntimeException) failure
                        : new IllegalStateException(failure);
            }
            synchronized (DuelPolicyRegistry.class) {
                OnDeviceOrtPolicy raced = POLICIES.get(aiId);
                if (raced != null && spec.modelSha256.equals(SHA256.get(aiId))) {
                    prepared.close();
                    return new DuelLease(raced, aiId);
                }
                if (raced != null) {
                    raced.close();
                }
                POLICIES.put(aiId, prepared);
                SHA256.put(aiId, spec.modelSha256);
                Log.i(TAG, "Duel policy ready ai=" + aiId + " sha256=" + spec.modelSha256);
                return new DuelLease(prepared, aiId);
            }
        }, LOADER);
    }

    /** Release every policy opened for a duel. Safe to call more than once. */
    public static void closeAll() {
        List<OnDeviceOrtPolicy> doomed;
        synchronized (DuelPolicyRegistry.class) {
            doomed = new ArrayList<>(POLICIES.values());
            POLICIES.clear();
            SHA256.clear();
        }
        for (OnDeviceOrtPolicy policy : doomed) {
            policy.close();
        }
        if (!doomed.isEmpty()) {
            Log.i(TAG, "Duel policies released count=" + doomed.size());
        }
    }
}
