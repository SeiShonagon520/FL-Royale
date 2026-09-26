package com.firstlight.offline;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;

/**
 * One APK-internal worker session to the probe's loopback-only control socket.
 *
 * <p>This class owns transport only. The caller validates JSON schema,
 * generation, observed tick, actor FAIR identity and action receipts. The
 * current server is serial, so one probe worker owns this connection for the
 * entire match. No host-side ADB port forward is used.</p>
 */
public final class LoopbackProbeSession implements AutoCloseable {
    private static final int DEFAULT_PORT = 26789;
    private static final int CONNECT_TIMEOUT_MS = 3000;
    private static final int RESPONSE_TIMEOUT_MS = 30000;
    private static final int MAX_COMMAND_BYTES = 4095;
    private static final int MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
    private static final Pattern SESSION_OK = Pattern.compile("\\\"ok\\\"\\s*:\\s*true");
    private static final Pattern SESSION_NAME = Pattern.compile(
            "\\\"session\\\"\\s*:\\s*\\\"control-session\\.v1\\\"");

    private final Socket socket;
    private final InputStream input;
    private final OutputStream output;
    private final Thread ownerThread;
    private final byte[] readBuffer = new byte[64 * 1024];
    private int readStart;
    private int readEnd;
    private volatile boolean closed;

    public LoopbackProbeSession() throws IOException {
        this(DEFAULT_PORT);
    }

    // Package-visible so the isolated protocol test can use an ephemeral port.
    LoopbackProbeSession(int port) throws IOException {
        if (port <= 0 || port > 65535) {
            throw new IllegalArgumentException("invalid probe port");
        }
        ownerThread = Thread.currentThread();
        socket = new Socket();
        try {
            socket.connect(new InetSocketAddress(InetAddress.getByAddress(
                    new byte[] {127, 0, 0, 1}), port), CONNECT_TIMEOUT_MS);
            socket.setTcpNoDelay(true);
            socket.setSoTimeout(RESPONSE_TIMEOUT_MS);
            input = new BufferedInputStream(socket.getInputStream());
            output = new BufferedOutputStream(socket.getOutputStream());
            writeLine("session-v1");
            String handshake = readLine(4096);
            if (!SESSION_OK.matcher(handshake).find() ||
                    !SESSION_NAME.matcher(handshake).find()) {
                throw new IOException("probe rejected control-session.v1 handshake");
            }
        } catch (IOException | RuntimeException error) {
            socket.close();
            throw error;
        }
    }

    public String request(String command) throws IOException {
        if (Thread.currentThread() != ownerThread) {
            throw new IllegalStateException("probe session belongs to its AI worker thread");
        }
        if (closed) {
            throw new IOException("probe session is closed");
        }
        writeLine(command);
        return readLine(MAX_RESPONSE_BYTES);
    }

    /** Request an atomic ordinary + rich stock-battle capture at one native tick.
     * Generation zero means the next observed normal battle; later requests
     * should use the generation returned by the capture itself.
     */
    public String requestNormalAt(long generation, int nativeTick) throws IOException {
        validateNormalTarget(generation, nativeTick);
        return request("observe-normal-at " + generation + " " + nativeTick);
    }

    /** Arm before the stock UI starts a battle; an acknowledgement proves the
     * game-step mailbox is pending before the caller opens the native scene.
     */
    public String armNormalAt(long generation, int nativeTick) throws IOException {
        validateNormalTarget(generation, nativeTick);
        return request("arm-normal-at " + generation + " " + nativeTick);
    }

    /** Receive the atomic capture from a previously acknowledged arm. */
    public String awaitNormalCapture(long generation, int nativeTick) throws IOException {
        validateNormalTarget(generation, nativeTick);
        return request("await-normal-capture " + generation + " " + nativeTick);
    }

    /** Release an unclaimed arm when the UI cancels battle creation. */
    public String cancelNormalCapture(long generation, int nativeTick) throws IOException {
        validateNormalTarget(generation, nativeTick);
        return request("cancel-normal-capture " + generation + " " + nativeTick);
    }

    /** Clock-only probe for confirming a normal controller's 250 ms cadence
     * before its complete FAIR world graph becomes readable.
     */
    public String requestNormalTickAt(long generation, int nativeTick) throws IOException {
        validateNormalTarget(generation, nativeTick);
        return request("observe-normal-tick-at " + generation + " " + nativeTick);
    }

    private static void validateNormalTarget(long generation, int nativeTick) {
        if (generation < 0 || nativeTick < 0 || nativeTick % 5 != 0) {
            throw new IllegalArgumentException(
                    "normal capture requires generation >= 0 and a 5-tick boundary");
        }
    }

    public String normalObserveStatus() throws IOException {
        return request("normal-observe-status");
    }

    private void writeLine(String command) throws IOException {
        if (command == null || command.indexOf('\n') >= 0 || command.indexOf('\r') >= 0) {
            throw new IllegalArgumentException("probe command must be one line");
        }
        byte[] bytes = command.getBytes(StandardCharsets.UTF_8);
        if (bytes.length == 0 || bytes.length > MAX_COMMAND_BYTES) {
            throw new IllegalArgumentException("probe command exceeds server line bound");
        }
        output.write(bytes);
        output.write('\n');
        output.flush();
    }

    private String readLine(int maxBytes) throws IOException {
        ByteArrayOutputStream line = new ByteArrayOutputStream(4096);
        while (true) {
            if (readStart == readEnd) {
                readEnd = input.read(readBuffer);
                readStart = 0;
                if (readEnd < 0) {
                    throw new IOException("probe closed before response newline");
                }
            }
            int newline = readStart;
            while (newline < readEnd && readBuffer[newline] != '\n') {
                newline++;
            }
            int chunkBytes = newline - readStart;
            if (chunkBytes > maxBytes - line.size()) {
                throw new IOException("probe response exceeds size bound");
            }
            line.write(readBuffer, readStart, chunkBytes);
            readStart = newline < readEnd ? newline + 1 : newline;
            if (newline < readEnd) {
                return line.toString(StandardCharsets.UTF_8.name());
            }
        }
    }

    @Override
    public void close() throws IOException {
        if (!closed) {
            closed = true;
            socket.close();
        }
    }
}
