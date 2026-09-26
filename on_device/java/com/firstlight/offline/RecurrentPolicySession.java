package com.firstlight.offline;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.concurrent.Callable;
import java.util.concurrent.Future;
import java.util.concurrent.FutureTask;
import java.util.concurrent.RejectedExecutionException;

/**
 * Per-battle LSTM state around {@link OnDeviceOrtPolicy}'s fixed tensor ABI.
 *
 * <p>The caller supplies the 89 FAIR observation tensors in schema order. This
 * class appends hidden, cell and episode_start as inputs 89, 90 and 91. State
 * is changed only by the policy worker. A new battle invalidates older work
 * immediately, and its reset task must complete before the first step.</p>
 *
 * <p>Inference completing advances LSTM state. The caller still owns native
 * action decoding, dispatch and PreviousActionV4; call {@link #abortBattle(long)}
 * if any of those fail before scheduling another step.</p>
 */
public final class RecurrentPolicySession {
    public static final int OBSERVATION_COUNT = 89;
    public static final int STATE_WIDTH = 512;
    public static final long FIRST_INFERENCE_TICK = 90;
    public static final long INFERENCE_TICK_STRIDE = 5;

    public static final class Decision {
        public final long generation;
        public final long observedTick;
        public final OnDeviceOrtPolicy.StepResult policy;
        private final long epoch;

        private Decision(long generation, long observedTick, OnDeviceOrtPolicy.StepResult policy,
                         long epoch) {
            this.generation = generation;
            this.observedTick = observedTick;
            this.policy = policy;
            this.epoch = epoch;
        }
    }

    public static final class StaleBattleException extends IllegalStateException {
        private StaleBattleException() {
            super("Policy step belongs to a previous battle generation");
        }
    }

    private final OnDeviceOrtPolicy policy;
    private final Object lock = new Object();
    private final float[] hidden = new float[STATE_WIDTH];
    private final float[] cell = new float[STATE_WIDTH];
    private long epoch;
    private long generation;
    private long expectedTick = FIRST_INFERENCE_TICK;
    private boolean ready;
    private boolean failed;
    private boolean stepPending;
    private long nextStepToken;
    private long pendingStepToken;
    private boolean episodeStart = true;

    public RecurrentPolicySession(OnDeviceOrtPolicy policy) {
        if (policy == null) throw new IllegalArgumentException("Missing policy worker");
        this.policy = policy;
    }

    /**
     * Invalidate older steps now, then reset recurrent state on the AI worker.
     * Await this Future before submitting the first tick 90 observation.
     */
    public Future<Void> beginBattle(long newGeneration) {
        final long requestedEpoch;
        synchronized (lock) {
            requestedEpoch = ++epoch;
            generation = newGeneration;
            ready = false;
            failed = false;
            try {
                return policy.submitOnWorker(() -> {
                    synchronized (lock) {
                        if (epoch != requestedEpoch) throw new StaleBattleException();
                        java.util.Arrays.fill(hidden, 0f);
                        java.util.Arrays.fill(cell, 0f);
                        expectedTick = FIRST_INFERENCE_TICK;
                        episodeStart = true;
                        ready = true;
                    }
                    return null;
                });
            } catch (RejectedExecutionException error) {
                failIfCurrent(requestedEpoch);
                throw error;
            }
        }
    }

    /**
     * Deep-copy 89 observation tensors before enqueueing one policy step.
     * Accepted ticks are exactly 90, 95, 100, ... with no skipped step.
     */
    public Future<Decision> submitStep(long battleGeneration, long observedTick,
                                       byte[][] observationInputs) {
        final long submittedEpoch;
        final long submittedToken;
        final byte[][] snapshot;
        synchronized (lock) {
            if (!ready || failed || battleGeneration != generation) {
                throw new StaleBattleException();
            }
            if (stepPending) {
                throw new RejectedExecutionException("Policy step already pending");
            }
            if (observedTick != expectedTick) {
                failed = true;
                ready = false;
                throw new IllegalArgumentException("Expected policy tick " + expectedTick +
                        ", got " + observedTick);
            }
            try {
                snapshot = copyObservation(observationInputs);
            } catch (RuntimeException error) {
                failed = true;
                ready = false;
                throw error;
            }
            submittedEpoch = epoch;
            stepPending = true;
            submittedToken = ++nextStepToken;
            pendingStepToken = submittedToken;
        }

        FutureTask<Decision> task = new FutureTask<Decision>(new Callable<Decision>() {
            @Override
            public Decision call() throws Exception {
                try {
                    final byte[][] inputs = new byte[OnDeviceOrtPolicy.INPUT_COUNT][];
                    synchronized (lock) {
                        if (epoch != submittedEpoch || !ready || failed) {
                            throw new StaleBattleException();
                        }
                        System.arraycopy(snapshot, 0, inputs, 0, OBSERVATION_COUNT);
                        inputs[89] = encodeState(hidden);
                        inputs[90] = encodeState(cell);
                        inputs[91] = new byte[] {(byte) (episodeStart ? 1 : 0)};
                    }
                    OnDeviceOrtPolicy.StepResult result = policy.runStepOnWorker(inputs);
                    synchronized (lock) {
                        if (epoch != submittedEpoch || !ready || failed) {
                            throw new StaleBattleException();
                        }
                        if (result.nextHidden.length != STATE_WIDTH ||
                                result.nextCell.length != STATE_WIDTH) {
                            throw new IllegalStateException("Invalid LSTM output width");
                        }
                        System.arraycopy(result.nextHidden, 0, hidden, 0, STATE_WIDTH);
                        System.arraycopy(result.nextCell, 0, cell, 0, STATE_WIDTH);
                        episodeStart = false;
                        expectedTick = Math.addExact(observedTick, INFERENCE_TICK_STRIDE);
                        // FutureTask publishes its result before run() returns.
                        // Release the slot now so get() -> next step is safe.
                        clearStepPendingLocked(submittedToken);
                    }
                    return new Decision(battleGeneration, observedTick, result, submittedEpoch);
                } catch (Exception error) {
                    failIfCurrent(submittedEpoch);
                    clearStepPending(submittedToken);
                    throw error;
                } catch (Error error) {
                    failIfCurrent(submittedEpoch);
                    clearStepPending(submittedToken);
                    throw error;
                }
            }
        }) {
            @Override
            public boolean cancel(boolean mayInterruptIfRunning) {
                // Never interrupt ORT native execution. Invalidate its output
                // before it can commit state or reach the action dispatcher.
                boolean cancelled = super.cancel(false);
                if (cancelled) failIfCurrent(submittedEpoch);
                return cancelled;
            }

            @Override
            public void run() {
                try {
                    super.run();
                } finally {
                    clearStepPending(submittedToken);
                }
            }
        };
        synchronized (lock) {
            if (epoch != submittedEpoch || !ready || failed) {
                clearStepPendingLocked(submittedToken);
                throw new StaleBattleException();
            }
            try {
                // Queue while holding the same lock as beginBattle. An old
                // step cannot slip behind the next battle's reset task.
                policy.submitOnWorker(() -> {
                    task.run();
                    return null;
                });
            } catch (RejectedExecutionException error) {
                failIfCurrent(submittedEpoch);
                clearStepPendingLocked(submittedToken);
                throw error;
            }
        }
        return task;
    }

    /** Abort this generation after inference, tensorization or dispatch failure. */
    public Future<Void> abortBattle(long battleGeneration) {
        final long abortEpoch;
        synchronized (lock) {
            if (battleGeneration != generation) throw new StaleBattleException();
            abortEpoch = ++epoch;
            ready = false;
            failed = true;
        }
        return policy.submitOnWorker(() -> {
            synchronized (lock) {
                if (epoch == abortEpoch) {
                    java.util.Arrays.fill(hidden, 0f);
                    java.util.Arrays.fill(cell, 0f);
                    episodeStart = true;
                }
            }
            return null;
        });
    }

    /** Check a returned decision immediately before decoding or dispatch. */
    public boolean isCurrent(Decision decision) {
        synchronized (lock) {
            return decision != null && ready && !failed &&
                    decision.epoch == epoch &&
                    decision.generation == generation &&
                    decision.observedTick + INFERENCE_TICK_STRIDE == expectedTick;
        }
    }

    private void failIfCurrent(long submittedEpoch) {
        synchronized (lock) {
            if (epoch == submittedEpoch) {
                ready = false;
                failed = true;
            }
        }
    }

    private void clearStepPending(long token) {
        synchronized (lock) {
            clearStepPendingLocked(token);
        }
    }

    private void clearStepPendingLocked(long token) {
        if (pendingStepToken == token) {
            stepPending = false;
            pendingStepToken = 0;
        }
    }

    private static byte[][] copyObservation(byte[][] source) {
        if (source == null || source.length != OBSERVATION_COUNT) {
            throw new IllegalArgumentException("Expected 89 FAIR observation tensors");
        }
        byte[][] copy = new byte[OBSERVATION_COUNT][];
        for (int i = 0; i < OBSERVATION_COUNT; i++) {
            if (source[i] == null) throw new IllegalArgumentException("Null FAIR tensor " + i);
            copy[i] = source[i].clone();
        }
        return copy;
    }

    private static byte[] encodeState(float[] state) {
        byte[] result = new byte[STATE_WIDTH * Float.BYTES];
        ByteBuffer.wrap(result).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer().put(state);
        return result;
    }
}
