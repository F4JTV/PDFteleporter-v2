// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

package org.pdfteleporter.app;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;

import androidx.core.content.FileProvider;

import java.io.File;

import org.qtproject.qt.android.bindings.QtActivity;

/**
 * Qt's activity, and what the phone offers around it: the documents other
 * applications hand over ("Ouvrir avec", "Partager"), and the share sheet
 * and viewers for the files the application makes. The C++ side calls the
 * static methods (app/platform.cpp).
 */
public class PdftActivity extends QtActivity {
    private static final String TAG = "PDFTeleporter";

    /** The document handed over and not yet taken by the interface. */
    private static String pendingUri;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Qt sets a light theme of its own unless one was set before it.
        setTheme(R.style.PdftTheme);
        // Kept before Qt starts: the interface asks once it is up.
        remember(getIntent());
        super.onCreate(savedInstanceState);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        // Qt keeps the intent the activity started with.
        setIntent(intent);
        remember(intent);
    }

    private static void remember(Intent intent) {
        if (intent == null)
            return;
        Uri uri = null;
        if (Intent.ACTION_VIEW.equals(intent.getAction()))
            uri = intent.getData();
        else if (Intent.ACTION_SEND.equals(intent.getAction()))
            uri = streamOf(intent);
        if (uri == null)
            return;
        synchronized (PdftActivity.class) {
            pendingUri = uri.toString();
        }
    }

    @SuppressWarnings("deprecation")
    private static Uri streamOf(Intent intent) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
            return intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri.class);
        return intent.getParcelableExtra(Intent.EXTRA_STREAM);
    }

    /** The document handed over since the last call (a content:// URI), or null. */
    public static String takePendingUri() {
        synchronized (PdftActivity.class) {
            String uri = pendingUri;
            pendingUri = null;
            return uri;
        }
    }

    /** A file of the application's storage, as the other applications may read it. */
    private static Uri uriFor(Context context, String path) {
        return FileProvider.getUriForFile(context, context.getPackageName() + ".qtprovider", new File(path));
    }

    /** Offers a file to the other applications: false when none could be offered it. */
    public static boolean shareFile(Context context, String path, String mime, String title) {
        try {
            Uri uri = uriFor(context, path);
            Intent send = new Intent(Intent.ACTION_SEND);
            send.setType(mime);
            send.putExtra(Intent.EXTRA_STREAM, uri);
            send.setClipData(ClipData.newRawUri(new File(path).getName(), uri));
            send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            Intent chooser = Intent.createChooser(send, title);
            chooser.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            start(context, chooser);
            return true;
        } catch (IllegalArgumentException | ActivityNotFoundException e) {
            Log.w(TAG, "Cannot share " + path, e);
            return false;
        }
    }

    /** Opens a file in the application the system has for its type: false when there is none. */
    public static boolean viewFile(Context context, String path, String mime) {
        try {
            Intent view = new Intent(Intent.ACTION_VIEW);
            view.setDataAndType(uriFor(context, path), mime);
            view.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            start(context, view);
            return true;
        } catch (IllegalArgumentException | ActivityNotFoundException e) {
            Log.w(TAG, "Cannot open " + path, e);
            return false;
        }
    }

    private static void start(Context context, Intent intent) {
        if (!(context instanceof Activity))
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);
    }
}
