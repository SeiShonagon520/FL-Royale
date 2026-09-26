package com.firstlight.offline;

import android.content.Context;
import android.util.Log;

import org.json.JSONObject;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Iterator;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Owns one stock-battle probe connection on a thread separate from the ORT worker.
 *
 * <p>This is a coordinator, not a FAIR encoder or a native action implementation.
 * Production must supply both {@link FairObservationAdapter} and
 * {@link OpponentActionSink}. The adapter must retain the same-frame candidate
 * projection and PreviousAction state in its {@link FrameTicket}; the sink must
 * validate that ticket and native battle generation at the actual command
 * consumption boundary. No default implementations are provided.</p>
 */
public final class OfflineBattleCoordinator implements AutoCloseable {
    private static final String TAG = "FirstlightBattleWorker";
    private static final String CAPTURE_SCHEMA = "native-normal-tick-capture.v1";
    private static final long FIRST_TICK = 0;
    private static final long FIRST_INFERENCE_TICK = RecurrentPolicySession.FIRST_INFERENCE_TICK;
    private static final long TICK_STRIDE = RecurrentPolicySession.INFERENCE_TICK_STRIDE;
    private static final int MAX_PENDING_CONSUMPTIONS = 16;

    /** Opaque owner of raw-to-FAIR projection and candidate-history state. */
    public interface FrameTicket extends AutoCloseable {
        long generation();
        long observedTick();
        int actorOwner();
        @Override void close() throws Exception;
    }

    /**
     * Must update the real enemy public tracker at ticks 0, 5, ... 85 before
     * producing the first 89 tensors at tick 90. Each call owns one frame
     * ticket and its candidate/PreviousAction history.
     */
    public interface FairObservationAdapter {
        FrameTicket observeWarmup(NativeCapture capture) throws Exception;
        FairObservation encodeEnemy(NativeCapture capture) throws Exception;
    }

    /**
     * Commits the decision and PreviousAction state, then promptly returns a
     * native queue receipt. ACT provides an asynchronous consumption Future;
     * WAIT commits state without submitting a native command. The Future must
     * not retain the frame ticket after dispatch returns.
     */
    public interface OpponentActionSink {
        ActionReceipt dispatch(RecurrentPolicySession.Decision decision,
                               FrameTicket frame, BattleGuard guard) throws Exception;
    }

    /** Call immediately before native dispatch; native must recheck the token. */
    public interface BattleGuard {
        boolean isCurrent();
    }

    public static final class FairObservation implements AutoCloseable {
        public final byte[][] orderedInputs;
        public final FrameTicket frame;

        public FairObservation(byte[][] orderedInputs, FrameTicket frame) {
            this.orderedInputs = Objects.requireNonNull(orderedInputs, "orderedInputs");
            this.frame = Objects.requireNonNull(frame, "frame");
        }

        @Override public void close() throws Exception {
            frame.close();
        }
    }

    /** Queue receipt for one committed decision; WAIT has no native command. */
    public static final class ActionReceipt {
        public final long generation;
        public final long observedTick;
        public final long queuedTick;
        public final long lastDueTick;
        public final long[] candidateUids;
        public final boolean committed;
        public final Future<ConsumptionReceipt> consumption;

        public ActionReceipt(long generation, long observedTick, long queuedTick,
                             long lastDueTick, long[] candidateUids,
                             boolean committed, Future<ConsumptionReceipt> consumption) {
            this.generation = generation;
            this.observedTick = observedTick;
            this.queuedTick = queuedTick;
            this.lastDueTick = lastDueTick;
            this.candidateUids = candidateUids == null ? null : candidateUids.clone();
            this.committed = committed;
            this.consumption = consumption;
        }
    }

    /** Native proof that every queued ACT command reached the consumption hook. */
    public static final class ConsumptionReceipt {
        public final long generation;
        public final long observedTick;
        public final long lastConsumedTick;
        public final long[] candidateUids;
        public final boolean confirmed;

        public ConsumptionReceipt(long generation, long observedTick, long lastConsumedTick,
                                  long[] candidateUids, boolean confirmed) {
            this.generation = generation;
            this.observedTick = observedTick;
            this.lastConsumedTick = lastConsumedTick;
            this.candidateUids = candidateUids == null ? null : candidateUids.clone();
            this.confirmed = confirmed;
        }
    }

    public static final class NativeCapture {
        public final long generation;
        public final long tick;
        public final int opponentOwner;
        public final JSONObject ordinary;
        public final JSONObject rich;

        private NativeCapture(long generation, long tick, int opponentOwner,
                              JSONObject ordinary, JSONObject rich) {
            this.generation = generation;
            this.tick = tick;
            this.opponentOwner = opponentOwner;
            this.ordinary = ordinary;
            this.rich = rich;
        }

        public boolean ended() {
            return ordinary.optBoolean("ended", false) || ordinary.optBoolean("finalized", false);
        }
    }

    public static final class BattleOutcome {
        public final long generation;
        public final long lastObservedTick;
        public final int decisions;

        private BattleOutcome(long generation, long lastObservedTick, int decisions) {
            this.generation = generation;
            this.lastObservedTick = lastObservedTick;
            this.decisions = decisions;
        }
    }

    /** UI may open the stock scene only after armed completes successfully. */
    public final class BattleLaunch {
        public final Future<Void> armed;
        public final Future<BattleOutcome> outcome;
        private final Battle battle;

        private BattleLaunch(Battle battle, Future<BattleOutcome> outcome) {
            this.battle = battle;
            this.armed = battle.armed;
            this.outcome = outcome;
        }

        public boolean isCurrent() {
            return OfflineBattleCoordinator.this.isCurrent(battle);
        }

        public void cancel() {
            OfflineBattleCoordinator.this.stopBattle(battle);
        }
    }

    private static final class Battle {
        final long token;
        final String aiId;
        final int opponentOwner;
        final GameOrtBootstrap.PolicyLease lease;
        final CompletableFuture<Void> armed = new CompletableFuture<>();
        volatile LoopbackProbeSession probe;
        volatile RecurrentPolicySession recurrent;
        volatile long nativeGeneration;

        Battle(long token, String aiId, int opponentOwner,
               GameOrtBootstrap.PolicyLease lease) {
            this.token = token;
            this.aiId = aiId;
            this.opponentOwner = opponentOwner;
            this.lease = lease;
        }
    }

    private static final class LatencyWindow {
        private final long[] recentNanos = new long[64];
        private int count;
        private int next;

        void add(long nanos) {
            recentNanos[next] = nanos;
            next = (next + 1) % recentNanos.length;
            if (count < recentNanos.length) count++;
        }

        String p95Ms() {
            long[] ordered = Arrays.copyOf(recentNanos, count);
            Arrays.sort(ordered);
            int index = Math.max(0, (int) Math.ceil(count * 0.95) - 1);
            return ms(ordered[index]);
        }
    }

    private final ExecutorService control = Executors.newSingleThreadExecutor(command -> {
        Thread thread = new Thread(command, "firstlight-battle-control");
        thread.setDaemon(true);
        return thread;
    });
    private final Object lock = new Object();
    private final AtomicLong nextToken = new AtomicLong();
    private Battle active;
    private boolean closed;

    /**
     * Prepare the selected model off the UI thread. The bootstrap publishes it
     * only after its preload Future succeeds; no battle may be active.
     */
    public Future<Long> selectOpponent(Context context, String aiId) {
        final Context app = Objects.requireNonNull(context, "context").getApplicationContext();
        final OnDeviceOrtPolicy.ModelSpec spec = PolicyModelCatalog.forId(aiId);
        synchronized (lock) {
            ensureAvailable();
            if (active != null) throw new RejectedExecutionException("Battle is active");
            return control.submit(() -> {
                synchronized (lock) {
                    if (active != null || closed) {
                        throw new RejectedExecutionException("Battle started during model selection");
                    }
                }
                long startNs = System.nanoTime();
                long loadNs = GameOrtBootstrap.setModel(app, aiId, spec).get();
                GameOrtBootstrap.PolicyLease lease = GameOrtBootstrap.acquire();
                if (lease.policy == null || !aiId.equals(lease.aiId) ||
                        !GameOrtBootstrap.isCurrent(lease)) {
                    throw new IllegalStateException("Selected model changed during preload");
                }
                Log.i(TAG, "model ready ai=" + aiId + " generation=" + lease.generation +
                        " wait_ms=" + ms(System.nanoTime() - startNs));
                return loadNs;
            });
        }
    }

    /**
     * Arms capture at native tick 0. Await launch.armed off the UI thread;
     * only then may the stock UI start the native battle. The outcome Future
     * must also be observed by the caller. It fails closed on capture, FAIR,
     * inference or native-consumption errors. No UI or render thread waits.
     * The opponentOwner must come from a verified stock-scene identity.
     */
    public BattleLaunch startBattle(String aiId, int opponentOwner,
                                    FairObservationAdapter adapter, OpponentActionSink sink) {
        Objects.requireNonNull(adapter, "real FAIR adapter is required");
        Objects.requireNonNull(sink, "real opponent action sink is required");
        if (aiId == null || aiId.isEmpty()) throw new IllegalArgumentException("Missing opponent id");
        if (opponentOwner != 0 && opponentOwner != 1) {
            throw new IllegalArgumentException("Opponent owner must be verified as 0 or 1");
        }
        final Battle battle;
        synchronized (lock) {
            ensureAvailable();
            if (active != null) throw new RejectedExecutionException("Battle is already active");
            GameOrtBootstrap.PolicyLease lease = GameOrtBootstrap.acquire();
            if (lease.policy == null || !aiId.equals(lease.aiId) ||
                    !GameOrtBootstrap.isCurrent(lease)) {
                throw new IllegalStateException("Selected opponent has no current policy");
            }
            battle = new Battle(nextToken.incrementAndGet(), aiId, opponentOwner, lease);
            active = battle;
            try {
                Future<BattleOutcome> outcome = control.submit(() -> runBattle(battle, adapter, sink));
                return new BattleLaunch(battle, outcome);
            } catch (RejectedExecutionException error) {
                active = null;
                throw error;
            }
        }
    }

    private BattleOutcome runBattle(Battle battle, FairObservationAdapter adapter,
                                    OpponentActionSink sink) throws Exception {
        long tick = FIRST_TICK;
        int decisions = 0;
        LatencyWindow latencies = new LatencyWindow();
        List<ActionReceipt> pendingConsumptions = new ArrayList<>();
        try {
            // This await is on the independent control thread, never the ORT
            // worker on which RecurrentPolicySession schedules its reset/step.
            battle.lease.policy.preload().get();
            requireCurrent(battle);
            LoopbackProbeSession probe = new LoopbackProbeSession();
            battle.probe = probe;
            requireCurrent(battle);
            JSONObject arm = new JSONObject(probe.armNormalAt(0, 0));
            if (!arm.getBoolean("ok") ||
                    !"normal-capture-armed.v1".equals(arm.getString("schema")) ||
                    arm.getLong("generation") != 0 || arm.getLong("tick") != 0) {
                throw new IOException("Native tick-zero mailbox did not acknowledge arming");
            }
            requireCurrent(battle);
            battle.armed.complete(null);
            while (true) {
                long stepStartedNs = System.nanoTime();
                long captureStartedNs = stepStartedNs;
                String response = tick == 0 ? probe.awaitNormalCapture(0, 0) :
                        probe.requestNormalAt(battle.nativeGeneration, (int) tick);
                long receivedNs = System.nanoTime();
                NativeCapture capture = parseCapture(response, battle.nativeGeneration, tick,
                        battle.opponentOwner);
                long capturedNs = System.nanoTime();
                int responseChars = response.length();
                response = null;
                checkPendingConsumptions(pendingConsumptions, capture);
                if (battle.nativeGeneration == 0) {
                    battle.nativeGeneration = capture.generation;
                    RecurrentPolicySession recurrent = new RecurrentPolicySession(battle.lease.policy);
                    battle.recurrent = recurrent;
                    recurrent.beginBattle(capture.generation).get();
                }
                requireCurrent(battle);
                if (capture.ended()) {
                    if (!pendingConsumptions.isEmpty()) {
                        throw new IllegalStateException("Battle ended before opponent actions were consumed");
                    }
                    Log.i(TAG, "battle ended ai=" + battle.aiId + " generation=" +
                            capture.generation + " tick=" + tick + " decisions=" + decisions);
                    return new BattleOutcome(capture.generation, tick, decisions);
                }
                if (tick < FIRST_INFERENCE_TICK) {
                    try (FrameTicket warmup = adapter.observeWarmup(capture)) {
                        validateFrameTicket(warmup, capture);
                    }
                    long totalNs = System.nanoTime() - stepStartedNs;
                    latencies.add(totalNs);
                    Log.i(TAG, "warmup ai=" + battle.aiId + " generation=" + capture.generation +
                            " tick=" + tick + " chars=" + responseChars +
                            " socket_ms=" + ms(receivedNs - captureStartedNs) +
                            " json_ms=" + ms(capturedNs - receivedNs) +
                            " total_ms=" + ms(totalNs) + " p95_ms=" + latencies.p95Ms());
                    tick = Math.addExact(tick, TICK_STRIDE);
                    continue;
                }
                try (FairObservation observation = adapter.encodeEnemy(capture)) {
                    validateObservation(observation, capture);
                    requireCurrent(battle);
                    long encodedNs = System.nanoTime();
                    RecurrentPolicySession.Decision decision = battle.recurrent
                            .submitStep(capture.generation, tick, observation.orderedInputs).get();
                    requireCurrent(battle);
                    if (!battle.recurrent.isCurrent(decision)) {
                        throw new CancellationException("Recurrent decision became stale");
                    }
                    long inferredNs = System.nanoTime();
                    ActionReceipt receipt = sink.dispatch(decision, observation.frame,
                            () -> isCurrent(battle) && battle.recurrent.isCurrent(decision));
                    validateQueueReceipt(receipt, capture, decision);
                    requireCurrent(battle);
                    if (receipt.consumption != null) {
                        if (pendingConsumptions.size() >= MAX_PENDING_CONSUMPTIONS) {
                            throw new IllegalStateException("Native action confirmations are falling behind");
                        }
                        pendingConsumptions.add(receipt);
                    }
                    decisions++;
                    long totalNs = System.nanoTime() - stepStartedNs;
                    latencies.add(totalNs);
                    Log.i(TAG, "tick ai=" + battle.aiId + " generation=" + capture.generation +
                            " observed=" + tick + " queued=" + receipt.queuedTick +
                            " due=" + receipt.lastDueTick +
                            " gate=" + decision.policy.gate +
                            " chars=" + responseChars +
                            " socket_ms=" + ms(receivedNs - captureStartedNs) +
                            " json_ms=" + ms(capturedNs - receivedNs) +
                            " fair_ms=" + ms(encodedNs - capturedNs) +
                            " forward_ms=" + ms(decision.policy.forwardNanos) +
                            " infer_wait_ms=" + ms(inferredNs - encodedNs) +
                            " total_ms=" + ms(totalNs) + " p95_ms=" + latencies.p95Ms());
                }
                tick = Math.addExact(tick, TICK_STRIDE);
                if (tick > Integer.MAX_VALUE) throw new IllegalStateException("Native tick overflow");
            }
        } catch (Exception | Error failure) {
            battle.armed.completeExceptionally(failure);
            Log.e(TAG, "battle stopped closed ai=" + battle.aiId +
                    " generation=" + battle.nativeGeneration + " tick=" + tick, failure);
            throw failure;
        } finally {
            endBattle(battle);
        }
    }

    private static NativeCapture parseCapture(String response, long expectedGeneration,
                                              long expectedTick, int opponentOwner) throws Exception {
        JSONObject root = new JSONObject(response);
        if (!root.getBoolean("ok") || !CAPTURE_SCHEMA.equals(root.getString("schema")) ||
                !root.getBoolean("atomic") || !"normal-player".equals(root.getString("source"))) {
            throw new IOException("Native response is not an atomic normal-player capture");
        }
        long generation = root.getLong("generation");
        long tick = root.getLong("tick");
        if (generation <= 0 || (expectedGeneration != 0 && generation != expectedGeneration) ||
                tick != expectedTick) {
            throw new IOException("Native battle generation or tick changed during capture");
        }
        JSONObject ordinary = root.getJSONObject("ordinary");
        JSONObject rich = root.getJSONObject("rich");
        if (!ordinary.getBoolean("ok") || !rich.getBoolean("ok") ||
                ordinary.getLong("generation") != generation ||
                rich.getLong("generation") != generation ||
                ordinary.getLong("tick") != tick || rich.getLong("tick") != tick ||
                ordinary.getLong("stateEpoch") != rich.getLong("stateEpoch") ||
                ordinary.optBoolean("truncated", false) || rich.optBoolean("truncated", false) ||
                ordinary.getJSONArray("players").length() != 2 ||
                !"native-rich-telemetry.v3".equals(rich.getString("schema"))) {
            throw new IOException("Native ordinary/rich FAIR sources disagree");
        }
        return new NativeCapture(generation, tick, opponentOwner, ordinary, rich);
    }

    private static void validateObservation(FairObservation observation,
                                            NativeCapture capture) {
        if (observation == null || observation.frame == null ||
                observation.orderedInputs == null ||
                observation.orderedInputs.length != RecurrentPolicySession.OBSERVATION_COUNT) {
            throw new IllegalStateException("FAIR tensors do not match enemy capture");
        }
        validateFrameTicket(observation.frame, capture);
    }

    private static void validateFrameTicket(FrameTicket ticket, NativeCapture capture) {
        if (ticket == null || ticket.generation() != capture.generation ||
                ticket.observedTick() != capture.tick ||
                ticket.actorOwner() != capture.opponentOwner) {
            throw new IllegalStateException("FAIR frame ticket does not match enemy capture");
        }
    }

    private static void validateQueueReceipt(ActionReceipt receipt, NativeCapture capture,
                                             RecurrentPolicySession.Decision decision) {
        if (receipt == null || !receipt.committed ||
                receipt.generation != capture.generation ||
                receipt.observedTick != capture.tick) {
            throw new IllegalStateException("Opponent decision was not committed at the observed frame");
        }
        if (decision.policy.gate == 0) {
            if (decision.policy.microActionCount != 0 || receipt.queuedTick != -1 ||
                    receipt.lastDueTick != -1 || receipt.consumption != null ||
                    receipt.candidateUids == null || receipt.candidateUids.length != 0) {
                throw new IllegalStateException("WAIT must not enqueue a native command");
            }
        } else if (decision.policy.gate == 1) {
            if (decision.policy.microActionCount <= 0 ||
                    decision.policy.microActionCount > decision.policy.candidateUid.length ||
                    receipt.candidateUids == null ||
                    receipt.candidateUids.length != decision.policy.microActionCount ||
                    receipt.queuedTick < capture.tick || receipt.lastDueTick < receipt.queuedTick ||
                    receipt.consumption == null) {
                throw new IllegalStateException("ACT lacks a matching native queue receipt");
            }
            for (int i = 0; i < receipt.candidateUids.length; i++) {
                if (receipt.candidateUids[i] != decision.policy.candidateUid[i]) {
                    throw new IllegalStateException("Queued candidate UID differs from the policy decision");
                }
            }
        } else {
            throw new IllegalStateException("Policy gate is invalid");
        }
    }

    private static void checkPendingConsumptions(List<ActionReceipt> pending,
                                                  NativeCapture capture) throws Exception {
        for (Iterator<ActionReceipt> cursor = pending.iterator(); cursor.hasNext();) {
            ActionReceipt queued = cursor.next();
            if (queued.generation != capture.generation) {
                throw new IllegalStateException("Pending native action crossed a battle generation");
            }
            if (!queued.consumption.isDone()) {
                if (capture.tick >= queued.lastDueTick) {
                    throw new IllegalStateException("Native action was not consumed by its due tick");
                }
                continue;
            }
            ConsumptionReceipt consumed = queued.consumption.get();
            if (consumed == null || !consumed.confirmed ||
                    consumed.generation != queued.generation ||
                    consumed.observedTick != queued.observedTick ||
                    consumed.lastConsumedTick < queued.queuedTick ||
                    consumed.lastConsumedTick > queued.lastDueTick ||
                    consumed.lastConsumedTick > capture.tick ||
                    !Arrays.equals(consumed.candidateUids, queued.candidateUids)) {
                throw new IllegalStateException("Native action consumption receipt is stale or incomplete");
            }
            cursor.remove();
        }
    }

    private boolean isCurrent(Battle battle) {
        synchronized (lock) {
            if (closed || active != battle || battle.token != nextToken.get()) return false;
        }
        return GameOrtBootstrap.isCurrent(battle.lease);
    }

    private void requireCurrent(Battle battle) {
        if (!isCurrent(battle)) throw new CancellationException("Battle/model generation changed");
    }

    /** Invalidates immediately; a blocked loopback read is released by close. */
    public void stopBattle() {
        stopBattle(null);
    }

    private void stopBattle(Battle expected) {
        final Battle previous;
        synchronized (lock) {
            if (expected != null && active != expected) return;
            previous = active;
            active = null;
            nextToken.incrementAndGet();
        }
        if (previous != null) {
            previous.armed.completeExceptionally(new CancellationException("Battle stopped before start"));
            closeBattleResources(previous);
        }
    }

    private void endBattle(Battle battle) {
        synchronized (lock) {
            if (active == battle) {
                active = null;
                nextToken.incrementAndGet();
            }
        }
        closeBattleResources(battle);
    }

    private static void closeBattleResources(Battle battle) {
        RecurrentPolicySession recurrent = battle.recurrent;
        if (recurrent != null && battle.nativeGeneration > 0) {
            try {
                recurrent.abortBattle(battle.nativeGeneration);
            } catch (RuntimeException failure) {
                Log.w(TAG, "Could not queue recurrent reset", failure);
            }
        }
        LoopbackProbeSession probe = battle.probe;
        if (probe != null) {
            try {
                probe.close();
            } catch (IOException failure) {
                Log.w(TAG, "Could not close probe session", failure);
            }
        }
    }

    private void ensureAvailable() {
        if (closed) throw new RejectedExecutionException("Battle coordinator is closed");
    }

    private static String ms(long nanos) {
        return String.format(java.util.Locale.ROOT, "%.2f", nanos / 1_000_000.0);
    }

    @Override public void close() {
        synchronized (lock) {
            if (closed) return;
            closed = true;
        }
        stopBattle();
        control.shutdown();
    }
}
