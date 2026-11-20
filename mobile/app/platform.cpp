// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

#include "platform.h"

#include <QDesktopServices>
#include <QFileInfo>
#include <QUrl>

#ifdef Q_OS_ANDROID
#include <QCoreApplication>
#include <QJniEnvironment>
#include <QJniObject>
#include <QtCore/qcoreapplication_platform.h>
#endif

namespace pdft::app::platform {

namespace {
QString startup_argument;

#ifdef Q_OS_ANDROID
// The Java side: android/src/org/pdfteleporter/app/PdftActivity.java.
constexpr const char *kActivityClass = "org/pdfteleporter/app/PdftActivity";

bool call_file_method(const char *method, const QString &path, const QString &mime, const QString *title) {
    auto context = QNativeInterface::QAndroidApplication::context();
    QJniObject jpath = QJniObject::fromString(path);
    QJniObject jmime = QJniObject::fromString(mime);
    jboolean done = JNI_FALSE;
    if (title) {
        QJniObject jtitle = QJniObject::fromString(*title);
        done = QJniObject::callStaticMethod<jboolean>(
            kActivityClass, method,
            "(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)Z",
            context.object(), jpath.object<jstring>(), jmime.object<jstring>(), jtitle.object<jstring>());
    } else {
        done = QJniObject::callStaticMethod<jboolean>(
            kActivityClass, method, "(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;)Z",
            context.object(), jpath.object<jstring>(), jmime.object<jstring>());
    }
    QJniEnvironment env;
    if (env.checkAndClearExceptions())
        return false;
    return done == JNI_TRUE;
}
#endif
} // namespace

bool canShare() {
#ifdef Q_OS_ANDROID
    return true;
#else
    return false;
#endif
}

bool shareFile(const QString &path, const QString &mime, const QString &title) {
#ifdef Q_OS_ANDROID
    return call_file_method("shareFile", path, mime, &title);
#else
    Q_UNUSED(mime);
    Q_UNUSED(title);
    // No share sheet: show the file where it is.
    return QDesktopServices::openUrl(QUrl::fromLocalFile(QFileInfo(path).absolutePath()));
#endif
}

bool openFile(const QString &path, const QString &mime) {
#ifdef Q_OS_ANDROID
    return call_file_method("viewFile", path, mime, nullptr);
#else
    Q_UNUSED(mime);
    return QDesktopServices::openUrl(QUrl::fromLocalFile(path));
#endif
}

QString takeIncoming() {
#ifdef Q_OS_ANDROID
    QJniObject uri = QJniObject::callStaticObjectMethod(kActivityClass, "takePendingUri", "()Ljava/lang/String;");
    QJniEnvironment env;
    if (env.checkAndClearExceptions() || !uri.isValid())
        return {};
    return uri.toString();
#else
    QString argument = startup_argument;
    startup_argument.clear();
    return argument;
#endif
}

void setStartupArgument(const QString &argument) { startup_argument = argument; }

} // namespace pdft::app::platform
