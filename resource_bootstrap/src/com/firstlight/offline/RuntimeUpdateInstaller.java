package com.firstlight.offline;

import android.content.Context;
import android.os.StatFs;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.nio.channels.FileLock;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Locale;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/** Installs the signed APK's runtime update into the game's private update directory. */
public final class RuntimeUpdateInstaller {
    public interface Progress {
        void onProgress(int filesDone, int filesTotal);
    }

    private static final String ASSET_PREFIX = "firstlight/";
    private static final String ZIP_ASSET = ASSET_PREFIX + "runtime_update.zip";
    private static final String MANIFEST_ASSET = ASSET_PREFIX + "runtime_update.manifest.json";
    private static final String MARKER = ".firstlight_runtime_update.json";
    private static final String SCHEMA = "firstlight-runtime-update-bundle.v1";
    private static final long FREE_SPACE_RESERVE = 64L * 1024L * 1024L;
    private static final int BUFFER_BYTES = 64 * 1024;

    private RuntimeUpdateInstaller() {}

    /**
     * Runs on a background thread before GameApp starts. The APK and manifest are
     * trusted only after every extracted file has passed SHA-256 verification.
     * A complete existing game update is reused without any modification.
     */
    public static String ensureInstalled(Context context, Progress progress) throws IOException {
        File dataDir = new File(context.getApplicationInfo().dataDir).getCanonicalFile();
        File update = child(dataDir, "update");
        File staging = child(dataDir, "update.firstlight-staging");
        File backup = child(dataDir, "update.firstlight-backup");
        File lockPath = child(dataDir, ".firstlight-resource.lock");
        Manifest manifest = loadManifest(context);

        try (RandomAccessFile lockFile = new RandomAccessFile(lockPath, "rw");
             FileLock ignored = lockFile.getChannel().lock()) {
            // A prior process may have died between the two rename calls.
            if (!update.exists() && backup.exists() && !backup.renameTo(update)) {
                throw new IOException("Could not restore previous runtime update");
            }
            if (verifyDirectory(update, manifest)) {
                if (backup.exists()) {
                    deleteTree(backup, dataDir);
                }
                return "reused";
            }
            if (update.exists() && !hasOwnMarker(update, manifest) && !compatiblePartialUpdate(update, manifest)) {
                throw new IOException("Existing game update differs; preserving it unchanged");
            }
            if (backup.exists()) {
                if (hasOwnMarker(backup, manifest) || (update.exists() && hasOwnMarker(update, manifest))) {
                    deleteTree(backup, dataDir);
                } else {
                    throw new IOException("A previous update backup needs inspection");
                }
            }
            long available = new StatFs(dataDir.getAbsolutePath()).getAvailableBytes();
            long required = manifest.uncompressedBytes + FREE_SPACE_RESERVE;
            if (available < required) {
                throw new IOException("Need " + required + " free bytes, have " + available);
            }

            deleteTree(staging, dataDir);
            if (!staging.mkdir()) {
                throw new IOException("Could not create staging directory");
            }
            staging.setReadable(true, false);
            staging.setExecutable(true, false);
            boolean promoted = false;
            try {
                // A second independent read verifies the whole ZIP byte stream,
                // including metadata and the central directory.
                try (InputStream source = context.getAssets().open(ZIP_ASSET)) {
                    if (!manifest.bundleSha256.equals(sha256(source))) {
                        throw new IOException("Bundled update ZIP digest differs from manifest");
                    }
                }
                extract(context, staging, manifest, progress);
                writeMarker(staging, manifest);
                if (!verifyDirectory(staging, manifest)) {
                    throw new IOException("Extracted update failed post-write verification");
                }

                if (update.exists() && !update.renameTo(backup)) {
                    throw new IOException("Could not back up previous update");
                }
                if (!staging.renameTo(update)) {
                    if (backup.exists() && !backup.renameTo(update)) {
                        throw new IOException("Update promotion failed and rollback failed");
                    }
                    throw new IOException("Could not promote staged update");
                }
                update.setReadable(true, false);
                update.setExecutable(true, false);
                promoted = true;
                if (backup.exists()) {
                    deleteTree(backup, dataDir);
                }
                return "installed";
            } finally {
                if (!promoted) {
                    deleteTree(staging, dataDir);
                }
            }
        }
    }

    private static final class Manifest {
        final String bundleSha256;
        final String contentVersion;
        final String fingerprintSha256;
        final JSONObject files;
        final int count;
        final long uncompressedBytes;

        Manifest(JSONObject json) throws JSONException, IOException {
            if (!SCHEMA.equals(json.getString("schema"))) {
                throw new IOException("Unknown runtime update manifest schema");
            }
            contentVersion = json.getString("runtime_content_version");
            bundleSha256 = json.getString("bundle_sha256");
            fingerprintSha256 = json.getString("fingerprint_sha256");
            files = json.getJSONObject("files_sha256");
            count = json.getInt("file_count");
            uncompressedBytes = json.getLong("uncompressed_bytes");
            if (count <= 0 || count != files.length() || uncompressedBytes <= 0
                    || !isDigest(bundleSha256) || !isDigest(fingerprintSha256)
                    || !fingerprintSha256.equals(files.getString("fingerprint.json"))) {
                throw new IOException("Invalid runtime update manifest totals or identity");
            }
            Iterator<String> names = files.keys();
            while (names.hasNext()) {
                String name = names.next();
                if (!safeName(name) || !isDigest(files.getString(name))) {
                    throw new IOException("Invalid update filename or digest: " + name);
                }
            }
        }
    }

    private static Manifest loadManifest(Context context) throws IOException {
        try (InputStream stream = context.getAssets().open(MANIFEST_ASSET)) {
            byte[] buffer = new byte[BUFFER_BYTES];
            java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
            int count;
            while ((count = stream.read(buffer)) != -1) {
                bytes.write(buffer, 0, count);
                if (bytes.size() > 1024 * 1024) {
                    throw new IOException("Runtime update manifest exceeds 1 MiB");
                }
            }
            return new Manifest(new JSONObject(bytes.toString("UTF-8")));
        } catch (JSONException exception) {
            throw new IOException("Malformed runtime update manifest", exception);
        }
    }

    private static void extract(Context context, File staging, Manifest manifest, Progress progress)
            throws IOException {
        Set<String> seen = new HashSet<>();
        int completed = 0;
        long total = 0;
        byte[] buffer = new byte[BUFFER_BYTES];
        try (ZipInputStream archive = new ZipInputStream(
                new BufferedInputStream(context.getAssets().open(ZIP_ASSET), BUFFER_BYTES))) {
            ZipEntry entry;
            while ((entry = archive.getNextEntry()) != null) {
                String name = entry.getName();
                if (entry.isDirectory() || !safeName(name) || !manifest.files.has(name)
                        || !seen.add(name)) {
                    throw new IOException("Unexpected update archive entry: " + name);
                }
                File output = child(staging, name);
                File parent = output.getParentFile();
                if (parent == null || (!parent.isDirectory() && !parent.mkdirs())) {
                    throw new IOException("Could not create update directory: " + parent);
                }
                parent.setReadable(true, false);
                parent.setExecutable(true, false);
                MessageDigest digest = newDigest();
                try (FileOutputStream raw = new FileOutputStream(output);
                     BufferedOutputStream file = new BufferedOutputStream(raw, BUFFER_BYTES)) {
                    int read;
                    while ((read = archive.read(buffer)) != -1) {
                        total += read;
                        if (total > manifest.uncompressedBytes) {
                            throw new IOException("Update archive exceeds expected size");
                        }
                        file.write(buffer, 0, read);
                        digest.update(buffer, 0, read);
                    }
                    file.flush();
                    raw.getFD().sync();
                }
                output.setReadable(true, false);
                try {
                    if (!hex(digest.digest()).equals(manifest.files.getString(name))) {
                        throw new IOException("Digest differs for update file: " + name);
                    }
                } catch (JSONException exception) {
                    throw new IOException("Entry missing from manifest: " + name, exception);
                }
                archive.closeEntry();
                completed++;
                if (progress != null) {
                    progress.onProgress(completed, manifest.count);
                }
            }
        }
        if (seen.size() != manifest.count || total != manifest.uncompressedBytes) {
            throw new IOException("Update archive is incomplete");
        }
    }

    private static boolean verifyDirectory(File directory, Manifest manifest) throws IOException {
        if (!directory.isDirectory()) {
            return false;
        }
        Iterator<String> names = manifest.files.keys();
        long total = 0;
        int count = 0;
        int evictedCosmetics = 0;
        boolean installedByThisApp = hasOwnMarker(directory, manifest);
        while (names.hasNext()) {
            String name = names.next();
            File file = child(directory, name);
            if (!file.isFile()) {
                // libg evicts unused downloaded banners/emotes after startup.
                // Reinstalling them on every launch adds needless disk writes.
                // The complete ZIP was already verified on first installation;
                // every present file and all non-cosmetic assets remain strict.
                if(installedByThisApp && !file.exists()
                        && (name.startsWith("banners/") || name.startsWith("emotes/"))) {
                    evictedCosmetics++;
                    continue;
                }
                return false;
            }
            total += file.length();
            try {
                if (!manifest.files.getString(name).equals(sha256(file))) {
                    return false;
                }
            } catch (JSONException exception) {
                throw new IOException("Invalid manifest entry: " + name, exception);
            }
            count++;
        }
        return count + evictedCosmetics == manifest.count
                && (evictedCosmetics > 0 || total == manifest.uncompressedBytes);
    }

    private static boolean hasOwnMarker(File directory, Manifest manifest) throws IOException {
        File marker = child(directory, MARKER);
        if (!marker.isFile() || marker.length() > 1024) {
            return false;
        }
        try (FileInputStream source = new FileInputStream(marker)) {
            byte[] data = new byte[(int) marker.length()];
            if (source.read(data) != data.length) {
                return false;
            }
            JSONObject json = new JSONObject(new String(data, "UTF-8"));
            return SCHEMA.equals(json.optString("schema"))
                    && manifest.bundleSha256.equals(json.optString("bundle_sha256"));
        } catch (JSONException exception) {
            return false;
        }
    }

    // Earlier diagnostic installations cached only assets they had displayed.
    // Permit completing that exact content version, while rejecting changed
    // files or a different fingerprint before any existing file is replaced.
    private static boolean compatiblePartialUpdate(File directory, Manifest manifest) throws IOException {
        File fingerprint=child(directory,"fingerprint.json");
        if(!fingerprint.isFile() || !manifest.fingerprintSha256.equals(sha256(fingerprint))) return false;
        Iterator<String> names=manifest.files.keys();
        while(names.hasNext()) {
            String name=names.next();
            File file=child(directory,name);
            if(!file.exists()) continue;
            try {
                if(!file.isFile() || !manifest.files.getString(name).equals(sha256(file))) return false;
            } catch(JSONException exception) { throw new IOException("Invalid manifest entry: "+name,exception); }
        }
        return true;
    }

    private static void writeMarker(File directory, Manifest manifest) throws IOException {
        String value = "{\"schema\":\"" + SCHEMA + "\",\"runtime_content_version\":\""
                + manifest.contentVersion + "\",\"bundle_sha256\":\""
                + manifest.bundleSha256 + "\"}\n";
        try (FileOutputStream source = new FileOutputStream(child(directory, MARKER))) {
            source.write(value.getBytes("UTF-8"));
            source.getFD().sync();
        }
    }

    private static void deleteTree(File candidate, File dataDir) throws IOException {
        if (!candidate.exists()) {
            return;
        }
        String parent = dataDir.getCanonicalPath() + File.separator;
        if (!candidate.getCanonicalPath().startsWith(parent)) {
            throw new IOException("Refusing to delete outside app data");
        }
        File[] children = candidate.listFiles();
        if (candidate.isDirectory()) {
            if (children == null) {
                throw new IOException("Could not list staging directory");
            }
            for (File child : children) {
                deleteTree(child, dataDir);
            }
        }
        if (!candidate.delete()) {
            throw new IOException("Could not clean staging directory: " + candidate);
        }
    }

    private static File child(File root, String relative) throws IOException {
        File file = new File(root, relative).getCanonicalFile();
        if (!file.getPath().startsWith(root.getCanonicalPath() + File.separator)) {
            throw new IOException("Update file leaves app-private directory");
        }
        return file;
    }

    private static boolean safeName(String name) {
        if (name == null || name.isEmpty() || name.startsWith("/")
                || name.indexOf('\\') >= 0 || name.indexOf(':') >= 0) {
            return false;
        }
        for (String part : name.split("/", -1)) {
            if (part.isEmpty() || part.equals(".") || part.equals("..")) {
                return false;
            }
        }
        return true;
    }

    private static boolean isDigest(String digest) {
        return digest != null && digest.matches("[0-9a-f]{64}");
    }

    private static MessageDigest newDigest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    private static String sha256(File file) throws IOException {
        try (InputStream source = new BufferedInputStream(new FileInputStream(file), BUFFER_BYTES)) {
            return sha256(source);
        }
    }

    private static String sha256(InputStream source) throws IOException {
        MessageDigest digest = newDigest();
        byte[] buffer = new byte[BUFFER_BYTES];
        int count;
        while ((count = source.read(buffer)) != -1) {
            digest.update(buffer, 0, count);
        }
        return hex(digest.digest());
    }

    private static String hex(byte[] bytes) {
        StringBuilder builder = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            builder.append(String.format(Locale.ROOT, "%02x", value & 0xff));
        }
        return builder.toString();
    }
}
