// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// PDF Teleporter for phones: the engine of the desktop version in C++, under
// a Qt Quick interface.

#include "backend.h"
#include "fonts.h"
#include "platform.h"

#include <QFile>
#include <QGuiApplication>
#include <QLibraryInfo>
#include <QLocale>
#include <QQmlApplicationEngine>
#include <QQuickStyle>
#include <QQuickWindow>
#include <QTimer>
#include <QTranslator>
#include <QUrl>

#ifndef Q_OS_ANDROID
#include "tour.h"

#include <QCommandLineParser>
#endif

namespace {

// The engine's own files (fonts/Carlito-*.ttf) come from the resources
// compiled into the program, on every platform.
std::optional<std::string> load_engine_resource(const std::string &path) {
    QFile file(QStringLiteral(":/engine/") + QString::fromStdString(path));
    if (!file.open(QIODevice::ReadOnly))
        return std::nullopt;
    const QByteArray data = file.readAll();
    return std::string(data.constData(), static_cast<size_t>(data.size()));
}

} // namespace

int main(int argc, char *argv[]) {
    QGuiApplication app(argc, argv);
    QGuiApplication::setOrganizationName(QStringLiteral("PDFteleporter"));
    QGuiApplication::setApplicationName(QStringLiteral("PDFteleporter"));
    QGuiApplication::setApplicationDisplayName(QStringLiteral("PDF Teleporter"));
    QGuiApplication::setApplicationVersion(QStringLiteral(PDFT_VERSION));

    pdft::engine::set_resource_loader(load_engine_resource);
    QQuickStyle::setStyle(QStringLiteral("Material"));

    // The interface is French; so are Qt's own texts where Qt carries their
    // translation (the desktop file dialogs). Nothing is lost without it.
    QTranslator qt_translator;
    if (qt_translator.load(QLocale(QLocale::French, QLocale::France), QStringLiteral("qt"), QStringLiteral("_"),
                           QLibraryInfo::path(QLibraryInfo::TranslationsPath)))
        QCoreApplication::installTranslator(&qt_translator);

#ifndef Q_OS_ANDROID
    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("PDF Teleporter, interface pour téléphone"));
    parser.addHelpOption();
    parser.addVersionOption();
    parser.addPositionalArgument(QStringLiteral("fichier"),
                                 QStringLiteral("Document à compresser, ou archive .psdi / .psdz à recomposer."));
    QCommandLineOption tour_option(
        QStringLiteral("tour"),
        QStringLiteral("Parcourt l'interface avec le document donné et enregistre une capture de chaque écran "
                       "dans le dossier indiqué (documentation, tests)."),
        QStringLiteral("dossier"));
    parser.addOption(tour_option);
    parser.process(app);
    const QStringList positional = parser.positionalArguments();
    if (!positional.isEmpty() && !parser.isSet(tour_option))
        pdft::app::platform::setStartupArgument(positional.first());
#endif

    // Made before the QML engine, destroyed after it.
    pdft::app::Backend backend(nullptr);

    QQmlApplicationEngine engine;
    engine.addImportPath(QStringLiteral("qrc:/qt/qml"));
    QObject::connect(
        &engine, &QQmlApplicationEngine::objectCreationFailed, &app, []() { QCoreApplication::exit(1); },
        Qt::QueuedConnection);
    engine.load(QUrl(QStringLiteral("qrc:/qt/qml/PdfTeleporter/Main.qml")));
    if (engine.rootObjects().isEmpty())
        return 1;

    // A document handed over while the application was in the background
    // ("Ouvrir avec", "Partager") is taken when it comes back to the front.
    QObject::connect(&app, &QGuiApplication::applicationStateChanged, &backend, [&backend](Qt::ApplicationState state) {
        if (state == Qt::ApplicationActive)
            backend.checkIncoming();
    });

#ifndef Q_OS_ANDROID
    if (parser.isSet(tour_option)) {
        auto *window = qobject_cast<QQuickWindow *>(engine.rootObjects().constFirst());
        const QString document = positional.value(0);
        QTimer::singleShot(0, &app, [window, &backend, document, dir = parser.value(tour_option)]() {
            QCoreApplication::exit(pdft::app::run_tour(window, backend, document, dir));
        });
    }
#endif

    return QGuiApplication::exec();
}
