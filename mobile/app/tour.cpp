// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

#include "tour.h"

#include "backend.h"

#include <QCoreApplication>
#include <QDir>
#include <QElapsedTimer>
#include <QFileInfo>
#include <QImage>
#include <QMetaObject>
#include <QQuickWindow>
#include <QThread>
#include <QUrl>
#include <QVariant>

#include <cstdio>
#include <functional>

namespace pdft::app {

namespace {

// Runs the event loop until `done` holds, or `timeout_ms` has passed.
bool wait_until(const std::function<bool()> &done, int timeout_ms) {
    QElapsedTimer timer;
    timer.start();
    while (!done()) {
        if (timer.elapsed() > timeout_ms)
            return false;
        QCoreApplication::processEvents(QEventLoop::AllEvents, 50);
        QThread::msleep(10);
    }
    return true;
}

// Lets animations end and the scene redraw.
void settle(int ms = 700) {
    wait_until([] { return false; }, ms);
}

class Tour {
public:
    Tour(QQuickWindow *window, Backend &backend, const QString &dir)
        : window_(window), backend_(backend), dir_(dir) {}

    bool ok() const { return ok_; }

    void shot(const QString &name, int settle_ms = 700) {
        settle(settle_ms);
        QImage image = window_->grabWindow();
        QString path = QDir(dir_).filePath(name + QStringLiteral(".png"));
        if (image.isNull() || !image.save(path)) {
            fail(QStringLiteral("capture impossible : %1").arg(path));
            return;
        }
        std::printf("%s\n", qPrintable(path));
    }

    void page(int index) {
        emit backend_.showPage(index);
        settle(400);
    }

    void call(const char *function) {
        QMetaObject::invokeMethod(window_, function);
        settle(400);
    }

    void call(const char *function, const QVariant &argument) {
        QMetaObject::invokeMethod(window_, function, Q_ARG(QVariant, argument));
        settle(400);
    }

    bool expect(bool condition, const QString &what) {
        if (!condition)
            fail(what);
        return condition;
    }

    void fail(const QString &message) {
        std::fprintf(stderr, "tour: %s\n", qPrintable(message));
        ok_ = false;
    }

private:
    QQuickWindow *window_;
    Backend &backend_;
    QString dir_;
    bool ok_ = true;
};

} // namespace

int run_tour(QQuickWindow *window, Backend &backend, const QString &document, const QString &out_dir) {
    if (!window || document.isEmpty() || !QFileInfo::exists(document)) {
        std::fprintf(stderr, "tour: un document existant est attendu\n");
        return 2;
    }
    QDir().mkpath(out_dir);
    Tour tour(window, backend, out_dir);

    // The tour changes the settings the way an operator would: they are put
    // back afterwards.
    const QString theme = backend.theme();
    const int quality = backend.qualityIndex();
    const bool text_only = backend.textOnly();
    backend.setTheme(QStringLiteral("light"));
    backend.setQualityIndex(2);
    backend.setTextOnly(false);

    tour.page(0);
    tour.shot(QStringLiteral("01-compresser-vide"));

    backend.openFile(QUrl::fromLocalFile(QFileInfo(document).absoluteFilePath()));
    tour.expect(wait_until([&] { return backend.estimateState() == QStringLiteral("ready"); }, 180000),
                QStringLiteral("pas d'estimation"));
    tour.shot(QStringLiteral("02-compresser-document"));
    tour.call("revealEnd", 0);
    tour.shot(QStringLiteral("03-compresser-estimation"));

    // Another quality, compressed at once: the progress window shows.
    backend.setQualityIndex(1);
    backend.compress();
    if (wait_until([&] { return backend.progress() > 0.15 || !backend.busy(); }, 60000) && backend.busy())
        tour.shot(QStringLiteral("04-compression-en-cours"), 60);
    tour.expect(wait_until([&] { return !backend.busy(); }, 300000), QStringLiteral("compression sans fin"));
    tour.expect(backend.hasArchive(), QStringLiteral("pas d'archive"));
    tour.shot(QStringLiteral("05-archive-prete"));

    tour.page(1);
    tour.shot(QStringLiteral("06-recomposer-vide"));
    backend.openReceived(backend.archiveUrl());
    tour.expect(backend.receivedValid(), QStringLiteral("archive refusée"));
    tour.shot(QStringLiteral("07-recomposer-archive"));
    backend.rebuild();
    tour.expect(wait_until([&] { return !backend.busy(); }, 300000), QStringLiteral("recomposition sans fin"));
    tour.expect(backend.hasPdf(), QStringLiteral("pas de PDF"));
    tour.shot(QStringLiteral("08-pdf-recompose"));

    tour.page(2);
    tour.shot(QStringLiteral("09-journal"));

    tour.call("showAbout");
    tour.shot(QStringLiteral("10-a-propos"));
    tour.call("closeDialogs");

    backend.setTheme(QStringLiteral("dark"));
    tour.page(0);
    tour.shot(QStringLiteral("11-sombre-compresser"));
    tour.page(1);
    tour.shot(QStringLiteral("12-sombre-recomposer"));

    backend.setTheme(QStringLiteral("red"));
    tour.page(0);
    tour.shot(QStringLiteral("13-rouge-compresser"));
    tour.page(1);
    tour.shot(QStringLiteral("14-rouge-recomposer"));
    tour.page(2);
    tour.shot(QStringLiteral("15-rouge-journal"));
    tour.call("showAbout");
    tour.shot(QStringLiteral("16-rouge-a-propos"));
    tour.call("closeDialogs");

    backend.setTheme(theme);
    backend.setQualityIndex(quality);
    backend.setTextOnly(text_only);
    settle(200);
    return tour.ok() ? 0 : 1;
}

} // namespace pdft::app
