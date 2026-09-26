package com.firstlight.offline;

import android.content.Context;
import android.content.res.AssetManager;
import android.util.Log;

import ai.onnxruntime.NodeInfo;
import ai.onnxruntime.OnnxJavaType;
import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OnnxValue;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtSession;
import ai.onnxruntime.TensorInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.LongBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.Callable;
import java.util.concurrent.Future;
import java.util.concurrent.FutureTask;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/**
 * APK-internal policy runner for the fixed 92-input/8-output Firstlight V4 ABI.
 *
 * <p>All model loading and inference happen on one private AI worker. The same
 * worker can own {@link LoopbackProbeSession}: construct and use that session
 * only inside {@link #submitOnWorker(Callable)} tasks. Do not call Future.get()
 * from inside this worker, as that would deadlock the serial queue.</p>
 *
 * <p>The caller owns battle generation/tick validation and the FAIR observation
 * tensorizer. This class accepts already encoded tensors in schema order and
 * returns copied primitive outputs; it does not read game state or dispatch
 * native actions.</p>
 */
public final class OnDeviceOrtPolicy implements AutoCloseable {
    private static final String TAG = "FirstlightOrtPolicy";
    public static final int INPUT_COUNT = 92;
    public static final int OUTPUT_COUNT = 8;
    private static final int QUEUED_TASK_LIMIT = 4;

    public static final class ModelSpec {
        public final String modelAsset;
        public final String modelSha256;
        public final String schemaAsset;
        public final String bindingAsset;

        public ModelSpec(String modelAsset, String modelSha256, String schemaAsset, String bindingAsset) {
            if (modelAsset == null || schemaAsset == null || bindingAsset == null ||
                    modelSha256 == null || !modelSha256.matches("[0-9a-f]{64}")) {
                throw new IllegalArgumentException("Invalid bundled model specification");
            }
            this.modelAsset = modelAsset;
            this.modelSha256 = modelSha256;
            this.schemaAsset = schemaAsset;
            this.bindingAsset = bindingAsset;
        }

        public static ModelSpec general() {
            return PolicyModelCatalog.forId("general");
        }
    }

    public static final class StepResult {
        public final long gate;
        public final long microActionCount;
        public final long[] candidateIndex;
        public final long[] candidateUid;
        public final long[] targetCell;
        public final long[] delayOffsetBin;
        public final float[] nextHidden;
        public final float[] nextCell;
        public final long modelLoadNanos;
        public final long forwardNanos;

        private StepResult(long gate, long microActionCount, long[] candidateIndex,
                           long[] candidateUid, long[] targetCell, long[] delayOffsetBin,
                           float[] nextHidden, float[] nextCell, long modelLoadNanos,
                           long forwardNanos) {
            this.gate = gate;
            this.microActionCount = microActionCount;
            this.candidateIndex = candidateIndex;
            this.candidateUid = candidateUid;
            this.targetCell = targetCell;
            this.delayOffsetBin = delayOffsetBin;
            this.nextHidden = nextHidden;
            this.nextCell = nextCell;
            this.modelLoadNanos = modelLoadNanos;
            this.forwardNanos = forwardNanos;
        }
    }

    private static final class TensorSpec {
        final int index;
        final String name;
        final String onnxName;
        final String dtype;
        final long[] shape;
        final int bytes;

        TensorSpec(int index, String name, String onnxName, String dtype, long[] shape, int bytes) {
            this.index = index;
            this.name = name;
            this.onnxName = onnxName;
            this.dtype = dtype;
            this.shape = shape;
            this.bytes = bytes;
        }
    }

    private final Context context;
    private final ModelSpec modelSpec;
    private final ThreadPoolExecutor worker;
    private long nextStepToken;
    private long pendingStepToken;
    private volatile Thread workerThread;
    private boolean closing;
    private OrtEnvironment environment;
    private OrtSession.SessionOptions sessionOptions;
    private OrtSession session;
    private TensorSpec[] inputSpecs;
    private TensorSpec[] outputSpecs;
    private long modelLoadNanos;
    private boolean loaded;

    public OnDeviceOrtPolicy(Context context, ModelSpec modelSpec) {
        if (context == null || modelSpec == null) throw new IllegalArgumentException("Missing context/model");
        this.context = context.getApplicationContext();
        this.modelSpec = modelSpec;
        this.worker = new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS,
                new ArrayBlockingQueue<>(QUEUED_TASK_LIMIT), command -> {
                    Thread thread = new Thread(() -> {
                        workerThread = Thread.currentThread();
                        command.run();
                    }, "firstlight-ai-worker");
                    thread.setDaemon(true);
                    return thread;
                }, new ThreadPoolExecutor.AbortPolicy()) {
            @Override
            protected void terminated() {
                if (session != null) {
                    try {
                        session.close();
                    } catch (Exception failure) {
                        Log.e(TAG, "Cannot close Firstlight ORT session", failure);
                    }
                }
                if (sessionOptions != null) sessionOptions.close();
            }
        };
    }

    /**
     * Copies the 92 mutable input byte arrays before enqueueing. At most one
     * policy step may be queued/running; callers must reject stale battle ticks.
     */
    public Future<StepResult> submitStep(byte[][] orderedInputs) {
        byte[][] snapshot = copyInputs(orderedInputs);
        synchronized (this) {
            ensureOpen();
            if (pendingStepToken != 0) {
                throw new RejectedExecutionException("Firstlight policy step already pending");
            }
            final long token = ++nextStepToken;
            pendingStepToken = token;
            FutureTask<StepResult> task = new FutureTask<StepResult>(() -> {
                try {
                    StepResult result = runStepOnWorker(snapshot);
                    // FutureTask.get() may return before run() exits. Release
                    // the slot before publishing the completed result.
                    clearPendingStep(token);
                    return result;
                } catch (Exception failure) {
                    clearPendingStep(token);
                    throw failure;
                } catch (Error failure) {
                    clearPendingStep(token);
                    throw failure;
                }
            }) {
                @Override
                public boolean cancel(boolean mayInterruptIfRunning) {
                    // A native ORT call must not be interrupted. Its output
                    // stays discarded if cancellation wins the Future race.
                    boolean cancelled = super.cancel(false);
                    if (cancelled) clearPendingStep(token);
                    return cancelled;
                }

                @Override
                public void run() {
                    try {
                        super.run();
                    } finally {
                        clearPendingStep(token);
                    }
                }
            };
            try {
                worker.execute(task);
            } catch (RejectedExecutionException failure) {
                clearPendingStep(token);
                throw failure;
            }
            return task;
        }
    }

    private synchronized void clearPendingStep(long token) {
        // A late old task may finish after its successor was accepted.
        if (pendingStepToken == token) pendingStepToken = 0;
    }

    /**
     * Start model verification and ORT session creation on the AI worker before
     * the first match. The Future returns cold model-load nanoseconds.
     */
    public synchronized Future<Long> preload() {
        ensureOpen();
        return worker.submit(() -> {
            try {
                ensureLoaded();
                Log.i(TAG, "model ready sha256=" + modelSpec.modelSha256 +
                        " load_ms=" + (modelLoadNanos / 1e6));
                return modelLoadNanos;
            } catch (Exception failure) {
                Log.e(TAG, "model preload failed", failure);
                throw failure;
            } catch (Error failure) {
                Log.e(TAG, "model preload failed", failure);
                throw failure;
            }
        });
    }

    /** Schedule loopback probe work on the same owner thread as inference. */
    public synchronized <T> Future<T> submitOnWorker(Callable<T> work) {
        ensureOpen();
        return worker.submit(work);
    }

    /** For a combined observe → infer → dispatch task already on this worker. */
    public StepResult runStepOnWorker(byte[][] orderedInputs) throws Exception {
        if (Thread.currentThread() != workerThread) {
            throw new IllegalStateException("Policy inference must run on its AI worker");
        }
        ensureLoaded();
        if (orderedInputs == null || orderedInputs.length != INPUT_COUNT) {
            throw new IllegalArgumentException("Expected 92 ordered policy tensors");
        }
        Map<String, OnnxTensor> inputs = new LinkedHashMap<>(INPUT_COUNT);
        try {
            for (int i = 0; i < INPUT_COUNT; i++) {
                TensorSpec spec = inputSpecs[i];
                byte[] raw = orderedInputs[i];
                if (raw == null || raw.length != spec.bytes) {
                    throw new IllegalArgumentException("Bad tensor bytes at " + i + " (" + spec.name + ")");
                }
                ByteBuffer data = ByteBuffer.allocateDirect(raw.length).order(ByteOrder.nativeOrder());
                data.put(raw).flip();
                inputs.put(spec.onnxName, OnnxTensor.createTensor(environment, data,
                        spec.shape, javaType(spec.dtype)));
            }
            long before = System.nanoTime();
            try (OrtSession.Result result = session.run(inputs)) {
                long elapsed = System.nanoTime() - before;
                if (result.size() != OUTPUT_COUNT) throw new IllegalStateException("Expected 8 policy outputs");
                long[] gate = readLongOutput(result, outputSpecs[0]);
                long[] count = readLongOutput(result, outputSpecs[1]);
                return new StepResult(gate[0], count[0],
                        readLongOutput(result, outputSpecs[2]),
                        readLongOutput(result, outputSpecs[3]),
                        readLongOutput(result, outputSpecs[4]),
                        readLongOutput(result, outputSpecs[5]),
                        readFloatOutput(result, outputSpecs[6]),
                        readFloatOutput(result, outputSpecs[7]),
                        modelLoadNanos, elapsed);
            }
        } finally {
            for (OnnxTensor tensor : inputs.values()) tensor.close();
        }
    }

    private void ensureLoaded() throws Exception {
        if (loaded) return;
        long before = System.nanoTime();
        readAbi();
        File modelFile = installVerifiedModel();
        environment = OrtEnvironment.getEnvironment();
        try {
            sessionOptions = new OrtSession.SessionOptions();
            sessionOptions.setIntraOpNumThreads(2);
            session = environment.createSession(modelFile.getAbsolutePath(), sessionOptions);
            if (session.getNumInputs() != INPUT_COUNT || session.getNumOutputs() != OUTPUT_COUNT) {
                throw new IllegalStateException("ONNX graph must have 92 inputs and 8 outputs");
            }
            Map<String, NodeInfo> graphInputs = session.getInputInfo();
            Map<String, NodeInfo> graphOutputs = session.getOutputInfo();
            for (TensorSpec spec : inputSpecs) checkNode(graphInputs, spec);
            for (TensorSpec spec : outputSpecs) checkNode(graphOutputs, spec);
            modelLoadNanos = System.nanoTime() - before;
            loaded = true;
        } catch (Exception failure) {
            if (session != null) {
                session.close();
                session = null;
            }
            if (sessionOptions != null) {
                sessionOptions.close();
                sessionOptions = null;
            }
            throw failure;
        }
    }

    private void readAbi() throws Exception {
        JSONObject schema = new JSONObject(readAssetText(modelSpec.schemaAsset));
        JSONObject binding = new JSONObject(readAssetText(modelSpec.bindingAsset));
        JSONArray schemaInputs = schema.getJSONArray("inputs");
        JSONArray schemaOutputs = schema.getJSONArray("outputs");
        JSONArray bindingInputs = binding.getJSONArray("inputs");
        JSONArray bindingOutputs = binding.getJSONArray("outputs");
        if (schemaInputs.length() != INPUT_COUNT || schemaOutputs.length() != OUTPUT_COUNT ||
                bindingInputs.length() != INPUT_COUNT || bindingOutputs.length() != OUTPUT_COUNT) {
            throw new IllegalStateException("Invalid Firstlight ABI count");
        }
        inputSpecs = new TensorSpec[INPUT_COUNT];
        outputSpecs = new TensorSpec[OUTPUT_COUNT];
        for (int i = 0; i < INPUT_COUNT; i++) {
            inputSpecs[i] = parseSpec(schemaInputs.getJSONObject(i), bindingInputs.getJSONObject(i), i, true);
        }
        for (int i = 0; i < OUTPUT_COUNT; i++) {
            outputSpecs[i] = parseSpec(schemaOutputs.getJSONObject(i), bindingOutputs.getJSONObject(i), i, false);
        }
    }

    private static TensorSpec parseSpec(JSONObject schema, JSONObject bound, int index, boolean input) throws Exception {
        String name = schema.getString("name");
        String dtype = schema.getString("dtype");
        long[] shape = shape(schema.getJSONArray("shape"));
        if (schema.getInt("index") != index || bound.getInt("index") != index ||
                !name.equals(bound.getString("schema_name")) ||
                !dtype.equals(bound.getString("dtype")) ||
                !Arrays.equals(shape, shape(bound.getJSONArray("shape")))) {
            throw new IllegalStateException("Policy ABI binding mismatch at " + index);
        }
        if (input && !dtype.equals("float32") && !dtype.equals("int64") && !dtype.equals("uint8")) {
            throw new IllegalStateException("Unsupported policy input dtype " + dtype);
        }
        if (!input && !dtype.equals("float32") && !dtype.equals("int64")) {
            throw new IllegalStateException("Unsupported policy output dtype " + dtype);
        }
        int size = input ? schema.getInt("bytes") : -1;
        return new TensorSpec(index, name, bound.getString("onnx_name"), dtype, shape, size);
    }

    private static long[] shape(JSONArray dimensions) throws Exception {
        long[] result = new long[dimensions.length()];
        for (int i = 0; i < result.length; i++) result[i] = dimensions.getLong(i);
        return result;
    }

    private static void checkNode(Map<String, NodeInfo> info, TensorSpec spec) {
        NodeInfo node = info.get(spec.onnxName);
        if (node == null || !(node.getInfo() instanceof TensorInfo)) {
            throw new IllegalStateException("Missing ONNX tensor " + spec.onnxName);
        }
        TensorInfo tensor = (TensorInfo) node.getInfo();
        if (!Arrays.equals(tensor.getShape(), spec.shape) || tensor.type != javaType(spec.dtype)) {
            throw new IllegalStateException("ONNX dtype/shape mismatch for " + spec.onnxName);
        }
    }

    private static OnnxJavaType javaType(String dtype) {
        switch (dtype) {
            case "float32": return OnnxJavaType.FLOAT;
            case "int64": return OnnxJavaType.INT64;
            case "uint8": return OnnxJavaType.UINT8;
            default: throw new IllegalArgumentException("Unsupported policy dtype " + dtype);
        }
    }

    private static long[] readLongOutput(OrtSession.Result result, TensorSpec spec) throws Exception {
        OnnxTensor tensor = output(result, spec);
        LongBuffer data = tensor.getLongBuffer();
        long[] copy = new long[data.remaining()];
        data.get(copy);
        return copy;
    }

    private static float[] readFloatOutput(OrtSession.Result result, TensorSpec spec) throws Exception {
        OnnxTensor tensor = output(result, spec);
        FloatBuffer data = tensor.getFloatBuffer();
        float[] copy = new float[data.remaining()];
        data.get(copy);
        return copy;
    }

    private static OnnxTensor output(OrtSession.Result result, TensorSpec spec) throws Exception {
        OnnxValue value = result.get(spec.onnxName).orElseThrow(
                () -> new IllegalStateException("Missing policy output " + spec.onnxName));
        OnnxTensor tensor = (OnnxTensor) value;
        TensorInfo info = (TensorInfo) tensor.getInfo();
        if (!Arrays.equals(info.getShape(), spec.shape) || info.type != javaType(spec.dtype)) {
            throw new IllegalStateException("Bad policy output " + spec.name);
        }
        return tensor;
    }

    private File installVerifiedModel() throws Exception {
        File directory = new File(context.getNoBackupFilesDir(), "firstlight-models");
        if (!directory.exists() && !directory.mkdirs()) throw new IllegalStateException("Cannot create model directory");
        File modelFile = new File(directory, modelSpec.modelSha256 + ".onnx");
        if (modelFile.isFile() && modelSpec.modelSha256.equals(sha256(modelFile))) return modelFile;
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream source = context.getAssets().open(modelSpec.modelAsset);
             FileOutputStream target = new FileOutputStream(modelFile, false)) {
            byte[] chunk = new byte[1024 * 1024];
            int count;
            while ((count = source.read(chunk)) != -1) {
                digest.update(chunk, 0, count);
                target.write(chunk, 0, count);
            }
        }
        String actual = hex(digest.digest());
        if (!modelSpec.modelSha256.equals(actual)) {
            if (!modelFile.delete()) Log.w(TAG, "Unable to remove invalid model file");
            throw new IllegalStateException("Bundled model SHA-256 mismatch: " + actual);
        }
        return modelFile;
    }

    private String readAssetText(String path) throws Exception {
        AssetManager assets = context.getAssets();
        try (InputStream source = assets.open(path);
             ByteArrayOutputStream result = new ByteArrayOutputStream()) {
            byte[] chunk = new byte[16 * 1024];
            int count;
            while ((count = source.read(chunk)) != -1) result.write(chunk, 0, count);
            return result.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static String sha256(File path) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (FileInputStream source = new FileInputStream(path)) {
            byte[] chunk = new byte[1024 * 1024];
            int count;
            while ((count = source.read(chunk)) != -1) digest.update(chunk, 0, count);
        }
        return hex(digest.digest());
    }

    private static String hex(byte[] hash) {
        char[] alphabet = "0123456789abcdef".toCharArray();
        char[] text = new char[hash.length * 2];
        for (int i = 0; i < hash.length; i++) {
            text[i * 2] = alphabet[(hash[i] >>> 4) & 15];
            text[i * 2 + 1] = alphabet[hash[i] & 15];
        }
        return new String(text);
    }

    private static byte[][] copyInputs(byte[][] orderedInputs) {
        if (orderedInputs == null || orderedInputs.length != INPUT_COUNT) {
            throw new IllegalArgumentException("Expected 92 ordered policy tensors");
        }
        byte[][] copy = new byte[INPUT_COUNT][];
        for (int i = 0; i < INPUT_COUNT; i++) {
            if (orderedInputs[i] == null) throw new IllegalArgumentException("Null tensor " + i);
            copy[i] = orderedInputs[i].clone();
        }
        return copy;
    }

    private void ensureOpen() {
        if (closing) throw new RejectedExecutionException("Firstlight policy is closing");
    }

    /** Queued work finishes before worker teardown; no new work is accepted. */
    @Override
    public synchronized void close() {
        if (closing) return;
        closing = true;
        worker.shutdown();
    }
}
