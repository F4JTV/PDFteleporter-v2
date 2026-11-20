// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// What the QML interface talks to: the operator's choices, the conversions
// run on a worker thread, their progress and results, and the log. Every
// message shown to the operator is French; identifiers stay English.
#pragma once

#include "logmodel.h"

#include <QObject>
#include <QString>
#include <QStringList>
#include <QTimer>
#include <QUrl>
#include <QVariantList>
#include <QtQml/qqmlregistration.h>

#include <atomic>
#include <memory>
#include <optional>
#include <string>

class QQmlEngine;
class QJSEngine;

namespace pdft::app {

class Backend : public QObject {
    Q_OBJECT
    // "Backend" in QML: the one instance main() makes.
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(QString version READ version CONSTANT)
    Q_PROPERTY(QString theme READ theme WRITE setTheme NOTIFY themeChanged)
    Q_PROPERTY(bool canShare READ canShare CONSTANT)
    Q_PROPERTY(QObject *log READ log CONSTANT)
    Q_PROPERTY(int winlinkLimitKb READ winlinkLimitKb CONSTANT)

    // --- compression settings
    Q_PROPERTY(QVariantList qualities READ qualities CONSTANT)
    Q_PROPERTY(int qualityIndex READ qualityIndex WRITE setQualityIndex NOTIFY settingsChanged)
    Q_PROPERTY(bool textOnly READ textOnly WRITE setTextOnly NOTIFY settingsChanged)
    Q_PROPERTY(bool compatible READ compatible WRITE setCompatible NOTIFY settingsChanged)

    // --- the document to compress
    Q_PROPERTY(bool hasSource READ hasSource NOTIFY sourceChanged)
    Q_PROPERTY(QString sourceName READ sourceName NOTIFY sourceChanged)
    Q_PROPERTY(QString sourceInfo READ sourceInfo NOTIFY sourceChanged)
    Q_PROPERTY(bool sourceIsImage READ sourceIsImage NOTIFY sourceChanged)

    // --- the estimate of the archive it would make
    Q_PROPERTY(QString estimateState READ estimateState NOTIFY estimateChanged)   // none, running, ready, failed
    Q_PROPERTY(QString estimateSize READ estimateSize NOTIFY estimateChanged)
    Q_PROPERTY(QVariantList estimateTimes READ estimateTimes NOTIFY estimateChanged)
    Q_PROPERTY(bool estimateOverLimit READ estimateOverLimit NOTIFY estimateChanged)

    // --- the archive made
    Q_PROPERTY(bool hasArchive READ hasArchive NOTIFY archiveChanged)
    Q_PROPERTY(QString archiveName READ archiveName NOTIFY archiveChanged)
    Q_PROPERTY(QString archiveInfo READ archiveInfo NOTIFY archiveChanged)
    Q_PROPERTY(QString archiveNote READ archiveNote NOTIFY archiveChanged)

    // --- the archive received
    Q_PROPERTY(bool hasReceived READ hasReceived NOTIFY receivedChanged)
    Q_PROPERTY(bool receivedValid READ receivedValid NOTIFY receivedChanged)
    Q_PROPERTY(QString receivedName READ receivedName NOTIFY receivedChanged)
    Q_PROPERTY(QString receivedInfo READ receivedInfo NOTIFY receivedChanged)
    Q_PROPERTY(bool receivedCrcOk READ receivedCrcOk NOTIFY receivedChanged)

    // --- the PDF rebuilt
    Q_PROPERTY(bool hasPdf READ hasPdf NOTIFY pdfChanged)
    Q_PROPERTY(QString pdfName READ pdfName NOTIFY pdfChanged)
    Q_PROPERTY(QString pdfInfo READ pdfInfo NOTIFY pdfChanged)

    // --- work in progress
    Q_PROPERTY(bool busy READ busy NOTIFY busyChanged)
    Q_PROPERTY(QString busyTitle READ busyTitle NOTIFY busyChanged)
    Q_PROPERTY(double progress READ progress NOTIFY progressChanged)
    Q_PROPERTY(QString progressText READ progressText NOTIFY progressChanged)

public:
    // No default constructor: QML would make an instance of its own with
    // it, before looking for create().
    explicit Backend(QObject *parent);
    ~Backend() override;

    // The QML engine's way to the instance (QML_SINGLETON): the one main()
    // made, which stays owned by C++.
    static Backend *create(QQmlEngine *engine, QJSEngine *js);

    QString version() const;
    QString theme() const { return theme_; }
    void setTheme(const QString &theme);
    bool canShare() const;
    QObject *log() { return &log_; }
    int winlinkLimitKb() const;

    QVariantList qualities() const;
    int qualityIndex() const { return quality_index_; }
    void setQualityIndex(int index);
    bool textOnly() const { return text_only_; }
    void setTextOnly(bool on);
    bool compatible() const { return compatible_; }
    void setCompatible(bool on);

    bool hasSource() const { return !source_path_.isEmpty(); }
    QString sourceName() const { return source_name_; }
    QString sourceInfo() const { return source_info_; }
    bool sourceIsImage() const { return source_is_image_; }

    QString estimateState() const { return estimate_state_; }
    QString estimateSize() const { return estimate_size_; }
    QVariantList estimateTimes() const { return estimate_times_; }
    bool estimateOverLimit() const { return estimate_over_limit_; }

    bool hasArchive() const { return !archive_path_.isEmpty(); }
    QString archiveName() const { return archive_name_; }
    QString archiveInfo() const { return archive_info_; }
    QString archiveNote() const { return archive_note_; }
    // The archive made, in the application's storage (tests, the tour).
    QUrl archiveUrl() const { return archive_path_.isEmpty() ? QUrl() : QUrl::fromLocalFile(archive_path_); }

    bool hasReceived() const { return !received_path_.isEmpty(); }
    bool receivedValid() const { return received_valid_; }
    QString receivedName() const { return received_name_; }
    QString receivedInfo() const { return received_info_; }
    bool receivedCrcOk() const { return received_crc_ok_; }

    bool hasPdf() const { return !pdf_path_.isEmpty(); }
    QString pdfName() const { return pdf_name_; }
    QString pdfInfo() const { return pdf_info_; }

    bool busy() const { return busy_; }
    QString busyTitle() const { return busy_title_; }
    double progress() const { return progress_; }
    QString progressText() const { return progress_text_; }

    // A file the operator picked, or another application handed over: a
    // document to compress or an archive to rebuild, told apart by name.
    Q_INVOKABLE void openFile(const QUrl &url);
    Q_INVOKABLE void openSource(const QUrl &url);
    Q_INVOKABLE void openReceived(const QUrl &url);

    Q_INVOKABLE void compress();
    Q_INVOKABLE void rebuild();
    Q_INVOKABLE void cancel();

    Q_INVOKABLE void shareArchive();
    Q_INVOKABLE void saveArchiveTo(const QUrl &url);
    Q_INVOKABLE void sharePdf();
    Q_INVOKABLE void viewPdf();
    Q_INVOKABLE void savePdfTo(const QUrl &url);

    // Where "Enregistrer sous…" proposes to put `name`: on a phone the
    // system's picker takes the name alone; on a desktop, the folder the
    // desktop version prepares Winlink attachments in.
    Q_INVOKABLE QUrl suggestedSaveUrl(const QString &name) const;

    Q_INVOKABLE void copyLog();
    Q_INVOKABLE QString licenseText(const QString &which) const;

    // Files handed over by other applications since the last look.
    Q_INVOKABLE void checkIncoming();

signals:
    void themeChanged();
    void settingsChanged();
    void sourceChanged();
    void estimateChanged();
    void archiveChanged();
    void receivedChanged();
    void pdfChanged();
    void busyChanged();
    void progressChanged();
    // The page the interface should show: 0 compress, 1 rebuild.
    void showPage(int page);
    // A short message for the operator (snackbar).
    void notify(const QString &message);
    // A conversion failed: a message for a dialog.
    void failed(const QString &title, const QString &message);

private:
    struct Job;
    struct CompressResult;
    struct Settings {
        QString source;
        // Which opening of `source`: a document shared twice under one name
        // lands on one path in the cache, with other content.
        int generation = 0;
        QString quality;
        bool text_only = false;
        bool compact = false;
        bool operator==(const Settings &o) const {
            return source == o.source && generation == o.generation && quality == o.quality &&
                   text_only == o.text_only && compact == o.compact;
        }
    };

    Settings current_settings() const;
    QString quality_id() const;
    void say(const QString &message, const QString &level = QStringLiteral("info"));
    void set_busy(bool busy, const QString &title = QString());
    void set_progress(double progress, const QString &text);
    void schedule_estimate();
    void start_estimate();
    void start_compression(const Settings &settings, bool adopted);
    void finish_compression(const Settings &settings, std::shared_ptr<CompressResult> result, bool foreground);
    void apply_estimate(const CompressResult &result);
    void deliver_archive(const Settings &settings, const CompressResult &result);
    QString import_file(const QUrl &url, QString *display_name);
    QString output_dir(const QString &kind) const;
    void copy_to(const QString &from, const QUrl &to, const QString &what);
    void load_settings();
    void save_settings() const;

    LogModel log_;
    QString theme_ = QStringLiteral("light");
    int quality_index_ = 2;
    bool text_only_ = false;
    bool compatible_ = true;

    QString source_path_, source_name_, source_info_;
    bool source_is_image_ = false;
    int source_generation_ = 0;
    QString estimate_state_ = QStringLiteral("none");
    QString estimate_size_;
    QVariantList estimate_times_;
    bool estimate_over_limit_ = false;
    QTimer estimate_timer_;

    QString archive_path_, archive_name_, archive_info_, archive_note_;
    QString received_path_, received_name_, received_info_;
    bool received_valid_ = false;
    bool received_crc_ok_ = false;
    QString pdf_path_, pdf_name_, pdf_info_;

    bool busy_ = false;
    QString busy_title_;
    // A document handed over during a conversion, opened after it.
    QUrl pending_incoming_;
    double progress_ = 0;
    QString progress_text_;

    // The background estimate, and the result last made, which a
    // compression with the same settings takes as it is.
    std::shared_ptr<Job> estimate_job_;
    std::shared_ptr<Job> foreground_job_;
    std::optional<Settings> cached_settings_;
    std::shared_ptr<CompressResult> cached_result_;

    static Backend *instance_;
};

} // namespace pdft::app
