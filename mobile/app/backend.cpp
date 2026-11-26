// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

#include "backend.h"

#include "engine.h"
#include "format.h"
#include "platform.h"
#include "presets.h"

#include <QClipboard>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QGuiApplication>
#include <QJSEngine>
#include <QLocale>
#include <QMetaObject>
#include <QPointer>
#include <QSaveFile>
#include <QSettings>
#include <QStandardPaths>
#include <QThread>
#include <QVariantMap>

#include <chrono>
#include <exception>
#include <string_view>

namespace pdft::app {

namespace {

// The engine's identifiers and the names the operator reads.
const QStringList kQualityIds = {QStringLiteral("ultra_low"), QStringLiteral("low"), QStringLiteral("medium"),
                                 QStringLiteral("high")};

QString qs(const std::string &s) { return QString::fromStdString(s); }

// The engine (MuPDF, the image libraries) runs on threads of its own with the
// stack a desktop thread has: Android gives a thread 1 MB, Linux 8 MB, and
// the engine is tested with the latter.
constexpr uint kEngineStackSize = 8u * 1024 * 1024;

QString decimal_seconds(double value) { return QString::number(value, 'f', 1).replace(QLatin1Char('.'), QLatin1Char(',')); }

QString reason_label(const std::string &reason) {
    if (reason == "rotation")
        return QStringLiteral("page pivotée");
    if (reason == "scan")
        return QStringLiteral("document scanné");
    return qs(reason);
}

QString archive_kind(int version) {
    switch (version) {
    case psdi::ARCHIVE_VERSION_STRUCT:
        return QStringLiteral("structuré");
    case psdi::ARCHIVE_VERSION_IMAGE:
        return QStringLiteral("image de page");
    case psdi::ARCHIVE_VERSION_COMPACT_STRUCT:
        return QStringLiteral("structuré compact .psdz");
    case psdi::ARCHIVE_VERSION_COMPACT_IMAGE:
        return QStringLiteral("image de page compacte .psdz");
    default:
        return QStringLiteral("version %1").arg(version);
    }
}

bool is_archive_name(const QString &name) {
    QString lower = name.toLower();
    return lower.endsWith(QStringLiteral(".psdi")) || lower.endsWith(QStringLiteral(".psdz"));
}

// The extension a file's first bytes call for, or an empty string: a
// document shared from a messaging application often comes without one,
// and the engine tells an image from a PDF by its name.
QString sniffed_extension(const QByteArray &head) {
    auto starts = [&head](std::string_view magic, int offset = 0) {
        return head.size() >= offset + static_cast<int>(magic.size()) &&
               std::string_view(head.constData() + offset, magic.size()) == magic;
    };
    using namespace std::string_view_literals;
    if (starts("%PDF"sv))
        return QStringLiteral(".pdf");
    if (starts(psdi::ARCHIVE_MAGIC)) {
        try {
            return qs(psdi::extension_for(psdi::peek_version(std::string_view(head.constData(), head.size()))));
        } catch (const psdi::PsdiError &) {
            return QStringLiteral(".psdi");
        }
    }
    if (starts("\xFF\xD8\xFF"sv))
        return QStringLiteral(".jpg");
    if (starts("\x89PNG\r\n\x1A\n"sv))
        return QStringLiteral(".png");
    if (starts("BM"sv))
        return QStringLiteral(".bmp");
    if (starts("II*\0"sv) || starts("MM\0*"sv))
        return QStringLiteral(".tif");
    if (starts("RIFF"sv) && starts("WEBP"sv, 8))
        return QStringLiteral(".webp");
    return {};
}

bool has_known_extension(const QString &name) {
    return is_archive_name(name) || name.toLower().endsWith(QStringLiteral(".pdf")) ||
           engine::is_image_file(name.toStdString());
}

QByteArray read_head(const QString &name) {
    QFile in(name);
    if (!in.open(QIODevice::ReadOnly))
        return {};
    return in.read(16);
}

// A file name the phone's storage accepts, from a display name.
QString safe_name(QString name) {
    static const QString forbidden = QStringLiteral("/\\:*?\"<>|");
    for (QChar &c : name)
        if (forbidden.contains(c) || c.unicode() < 32)
            c = QLatin1Char('_');
    name = name.trimmed();
    return name.isEmpty() ? QStringLiteral("document") : name;
}

// What QFile opens: a path, or on Android a content:// URI, kept encoded as
// the system gave it (a document id may hold an encoded "/" or ":").
QString file_name_of(const QUrl &url) {
    return url.isLocalFile() ? url.toLocalFile() : url.toString(QUrl::FullyEncoded);
}

bool write_file(const QString &path, const std::string &data, QString *error) {
    QSaveFile out(path);
    if (!out.open(QIODevice::WriteOnly)) {
        *error = out.errorString();
        return false;
    }
    if (out.write(data.data(), static_cast<qint64>(data.size())) != static_cast<qint64>(data.size())) {
        *error = out.errorString();
        out.cancelWriting();
        return false;
    }
    if (!out.commit()) {
        *error = out.errorString();
        return false;
    }
    return true;
}

bool read_file(const QString &path, std::string *data, QString *error) {
    QFile in(path);
    if (!in.open(QIODevice::ReadOnly)) {
        *error = in.errorString();
        return false;
    }
    QByteArray bytes = in.readAll();
    data->assign(bytes.constData(), static_cast<size_t>(bytes.size()));
    return true;
}

} // namespace

// One conversion on a worker thread. "adopted" means the operator waits on
// it: its progress is shown, and its result is delivered.
struct Backend::Job {
    std::atomic<bool> cancel{false};
    std::atomic<bool> adopted{false};
    Settings settings;
    QPointer<QThread> thread;
};

struct Backend::CompressResult {
    bool ok = false;
    bool cancelled = false;
    QString error;
    engine::CompressReport report;
};

Backend *Backend::instance_ = nullptr;

Backend *Backend::create(QQmlEngine *, QJSEngine *) {
    Q_ASSERT(instance_);
    QJSEngine::setObjectOwnership(instance_, QJSEngine::CppOwnership);
    return instance_;
}

Backend::Backend(QObject *parent) : QObject(parent) {
    if (!instance_)
        instance_ = this;
    load_settings();
    estimate_timer_.setSingleShot(true);
    // Changing quality three times in a row must not launch three
    // compressions: only the setting the operator settles on is measured.
    estimate_timer_.setInterval(450);
    connect(&estimate_timer_, &QTimer::timeout, this, &Backend::start_estimate);
    say(QStringLiteral("Prêt. Sélectionnez un PDF ou une image à compresser, ou une archive .psdi ou .psdz à "
                       "recomposer."));
}

Backend::~Backend() {
    // A worker posting its result to a destroyed backend would crash on
    // exit: every one is stopped and waited for.
    for (const auto &job : {estimate_job_, foreground_job_})
        if (job)
            job->cancel = true;
    const auto threads = findChildren<QThread *>();
    for (QThread *thread : threads)
        thread->wait();
    if (instance_ == this)
        instance_ = nullptr;
}

QString Backend::version() const { return QStringLiteral(PDFT_VERSION); }

int Backend::winlinkLimitKb() const { return static_cast<int>(engine::WINLINK_MAX_ATTACHMENT / 1024); }

QUrl Backend::suggestedSaveUrl(const QString &name) const {
#ifdef Q_OS_ANDROID
    return QUrl::fromLocalFile(QLatin1Char('/') + name);
#else
    QString dir = QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation) +
                  QStringLiteral("/PDFteleporter");
    QDir().mkpath(dir);
    return QUrl::fromLocalFile(dir + QLatin1Char('/') + name);
#endif
}

void Backend::setTheme(const QString &theme) {
    if (theme == theme_)
        return;
    theme_ = theme;
    save_settings();
    emit themeChanged();
}

bool Backend::canShare() const { return platform::canShare(); }

QVariantList Backend::qualities() const {
    QVariantList out;
    for (const auto &preset : engine::presets()) {
        QVariantMap item;
        item[QStringLiteral("id")] = qs(preset.id);
        item[QStringLiteral("label")] = QString::fromUtf8(engine::quality_label(preset.id));
        item[QStringLiteral("description")] = qs(preset.description);
        out.append(item);
    }
    return out;
}

void Backend::setQualityIndex(int index) {
    if (index < 0 || index >= kQualityIds.size() || index == quality_index_)
        return;
    quality_index_ = index;
    save_settings();
    emit settingsChanged();
    schedule_estimate();
}

void Backend::setTextOnly(bool on) {
    if (on == text_only_)
        return;
    text_only_ = on;
    save_settings();
    emit settingsChanged();
    schedule_estimate();
}

void Backend::setCompatible(bool on) {
    if (on == compatible_)
        return;
    compatible_ = on;
    emit settingsChanged();
    schedule_estimate();
}

QString Backend::quality_id() const { return kQualityIds.value(quality_index_, QStringLiteral("medium")); }

Backend::Settings Backend::current_settings() const {
    return Settings{source_path_, source_generation_, quality_id(), text_only_, !compatible_};
}

// The log of the interface, copied to the system's (Android: logcat), where
// the last lines before an unexpected stop remain readable.
void Backend::say(const QString &message, const QString &level) {
    qInfo().noquote() << QStringLiteral("[%1] %2").arg(level, message);
    log_.append(message, level);
}

void Backend::set_busy(bool busy, const QString &title) {
    busy_ = busy;
    busy_title_ = title;
    if (busy) {
        progress_ = 0;
        progress_text_ = QStringLiteral("Préparation…");
        emit progressChanged();
    }
    emit busyChanged();
    if (!busy && !pending_incoming_.isEmpty()) {
        QUrl url = pending_incoming_;
        pending_incoming_.clear();
        QTimer::singleShot(0, this, [this, url]() { openFile(url); });
    }
}

void Backend::set_progress(double progress, const QString &text) {
    progress_ = progress;
    progress_text_ = text;
    emit progressChanged();
}

// --- files --------------------------------------------------------------------------------

QString Backend::output_dir(const QString &kind) const {
    QString dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation) + QLatin1Char('/') + kind;
    QDir().mkpath(dir);
    return dir;
}

QString Backend::import_file(const QUrl &url, QString *display_name) {
    QString name = file_name_of(url);
    qInfo().noquote() << "import" << name;
    QFileInfo info(name);
    *display_name = info.fileName();
    if (url.isLocalFile())
        return name;   // the desktop: the file where it is
    // A content:// document another application lends: copied into the
    // cache, under its own name, since the name tells an image from a PDF.
    QFile in(name);
    if (!in.open(QIODevice::ReadOnly)) {
        QString shown = display_name->isEmpty() ? QStringLiteral("du document") : *display_name;
        say(QStringLiteral("Lecture impossible de %1 : %2").arg(shown, in.errorString()), QStringLiteral("error"));
        emit failed(QStringLiteral("Lecture impossible"), in.errorString());
        return {};
    }
    QByteArray data = in.readAll();
    in.close();
    if (display_name->isEmpty())
        *display_name = QStringLiteral("document");
    *display_name = safe_name(*display_name);
    if (!has_known_extension(*display_name))
        *display_name += sniffed_extension(data.left(16));
    QString dir = QStandardPaths::writableLocation(QStandardPaths::CacheLocation) + QStringLiteral("/incoming/") +
                  (is_archive_name(*display_name) ? QStringLiteral("received") : QStringLiteral("source"));
    QDir(dir).removeRecursively();
    QDir().mkpath(dir);
    QString target = dir + QLatin1Char('/') + *display_name;
    QString error;
    if (!write_file(target, std::string(data.constData(), static_cast<size_t>(data.size())), &error)) {
        say(QStringLiteral("Copie impossible de %1 : %2").arg(*display_name, error), QStringLiteral("error"));
        return {};
    }
    return target;
}

void Backend::openFile(const QUrl &url) {
    QString name = QFileInfo(file_name_of(url)).fileName();
    // An archive by its name, or by its content when the name says nothing.
    bool archive = is_archive_name(name) ||
                   (!has_known_extension(name) && is_archive_name(sniffed_extension(read_head(file_name_of(url)))));
    if (archive) {
        openReceived(url);
        emit showPage(1);
    } else {
        openSource(url);
        emit showPage(0);
    }
}

void Backend::openSource(const QUrl &url) {
    QString display;
    QString path = import_file(url, &display);
    if (path.isEmpty())
        return;
    // A new document, perhaps under the name of the last one: nothing
    // measured on the last one stands.
    if (estimate_job_)
        estimate_job_->cancel = true;
    estimate_job_.reset();
    cached_settings_.reset();
    cached_result_.reset();
    ++source_generation_;
    source_path_ = path;
    source_name_ = display;
    long long size = QFileInfo(path).size();
    bool image = engine::is_image_file(path.toStdString()) || engine::is_image_file(display.toStdString());
    source_is_image_ = image;
    QString kind = image ? QStringLiteral("Image") : QStringLiteral("PDF");
    source_info_ = QStringLiteral("%1 · %2").arg(kind, qs(engine::format_bytes(size)));
    say(QStringLiteral("%1 sélectionné%2 : %3 (%4)")
            .arg(kind, image ? QStringLiteral("e") : QString(), display, qs(engine::format_bytes(size))));
    archive_path_.clear();
    emit archiveChanged();
    emit sourceChanged();
    schedule_estimate();
}

void Backend::openReceived(const QUrl &url) {
    QString display;
    QString path = import_file(url, &display);
    if (path.isEmpty())
        return;
    received_path_ = path;
    received_name_ = display;
    pdf_path_.clear();
    emit pdfChanged();

    // Validated on selection: the operator needs to know a file arrived
    // intact before spending time rebuilding it.
    std::string data;
    QString error;
    psdi::Validation report;
    if (!read_file(path, &data, &error)) {
        report.error = error.toStdString();
    } else {
        report = psdi::validate(data);
    }
    received_valid_ = report.valid;
    received_crc_ok_ = report.checksum_ok;
    if (!report.valid) {
        received_info_ = QStringLiteral("Archive invalide : %1").arg(qs(report.error));
        say(received_info_, QStringLiteral("error"));
    } else {
        QString crc = report.checksum_ok ? QStringLiteral("CRC correct") : QStringLiteral("CRC INCORRECT");
        received_info_ = QStringLiteral("Version %1 (%2) · %3 page(s) · %4")
                             .arg(report.version)
                             .arg(archive_kind(report.version))
                             .arg(report.pages)
                             .arg(crc);
        say(QStringLiteral("Archive chargée : %1 · %2 · %3").arg(display, archive_kind(report.version), crc),
            report.checksum_ok ? QStringLiteral("success") : QStringLiteral("warning"));
    }
    emit receivedChanged();
}

void Backend::checkIncoming() {
    QString incoming = platform::takeIncoming();
    if (incoming.isEmpty())
        return;
    // Android gives the URI encoded: it is taken as such, nothing decoded.
    QUrl url = incoming.contains(QStringLiteral("://")) ? QUrl::fromEncoded(incoming.toUtf8(), QUrl::StrictMode)
                                                        : QUrl::fromLocalFile(QFileInfo(incoming).absoluteFilePath());
    if (!url.isValid())
        url = QUrl(incoming);
    if (busy_) {
        // Opened once the conversion is over: opening it now would replace
        // the document being converted.
        pending_incoming_ = url;
        say(QStringLiteral("Fichier reçu : il sera ouvert à la fin de la conversion."));
        return;
    }
    openFile(url);
}

// --- compression ------------------------------------------------------------------------

void Backend::schedule_estimate() {
    if (source_path_.isEmpty()) {
        estimate_state_ = QStringLiteral("none");
        estimate_timer_.stop();
        emit estimateChanged();
        return;
    }
    if (cached_settings_ && *cached_settings_ == current_settings() && cached_result_) {
        apply_estimate(*cached_result_);
        return;
    }
    estimate_state_ = QStringLiteral("running");
    emit estimateChanged();
    estimate_timer_.start();
}

void Backend::start_estimate() {
    if (source_path_.isEmpty())
        return;
    // A real compression is running: its result will be more authoritative
    // than an estimate, so the CPU is left to it.
    if (busy_) {
        estimate_timer_.start();
        return;
    }
    Settings settings = current_settings();
    if (estimate_job_ && estimate_job_->settings == settings)
        return;
    if (estimate_job_)
        estimate_job_->cancel = true;
    start_compression(settings, false);
}

void Backend::start_compression(const Settings &settings, bool adopted) {
    auto job = std::make_shared<Job>();
    job->settings = settings;
    job->adopted = adopted;
    if (adopted)
        foreground_job_ = job;
    else
        estimate_job_ = job;

    engine::CompressOptions options;
    options.quality = settings.quality.toStdString();
    options.skip_images = settings.text_only;
    options.compact = settings.compact;
    options.source_name = (settings.source == source_path_ ? source_name_ : QFileInfo(settings.source).fileName())
                              .toStdString();
    const std::string path = settings.source.toStdString();

    QThread *thread = QThread::create([this, job, options, path, settings]() {
        auto result = std::make_shared<CompressResult>();
        engine::ProgressFn progress = [this, job](int step, int total, const std::string &message) {
            if (!job->adopted)
                return;
            double value = total > 0 ? static_cast<double>(step) / total : 0;
            QString text = qs(message);
            QMetaObject::invokeMethod(
                this, [this, job, value, text]() {
                    if (job == foreground_job_)
                        set_progress(value, text);
                },
                Qt::QueuedConnection);
        };
        try {
            result->report = engine::pdf_to_archive(path, options, progress, &job->cancel);
            result->ok = true;
        } catch (const engine::Cancelled &) {
            result->cancelled = true;
        } catch (const std::exception &e) {
            result->error = QString::fromUtf8(e.what());
        }
        QMetaObject::invokeMethod(
            this, [this, job, settings, result]() {
                bool foreground = job->adopted;
                if (job == estimate_job_)
                    estimate_job_.reset();
                if (job == foreground_job_)
                    foreground_job_.reset();
                finish_compression(settings, result, foreground);
            },
            Qt::QueuedConnection);
    });
    thread->setParent(this);
    thread->setStackSize(kEngineStackSize);
    connect(thread, &QThread::finished, thread, &QObject::deleteLater);
    job->thread = thread;
    thread->start(adopted ? QThread::NormalPriority : QThread::LowPriority);
}

void Backend::finish_compression(const Settings &settings, std::shared_ptr<CompressResult> result, bool foreground) {
    if (result->ok) {
        cached_settings_ = settings;
        cached_result_ = result;
    }
    if (foreground) {
        set_busy(false);
        if (result->cancelled) {
            say(QStringLiteral("Compression annulée."), QStringLiteral("warning"));
        } else if (!result->ok) {
            say(QStringLiteral("Échec de la compression : %1").arg(result->error), QStringLiteral("error"));
            emit failed(QStringLiteral("Échec de la compression"), result->error);
        } else {
            deliver_archive(settings, *result);
        }
    }
    // The estimate shown is that of the settings shown.
    if (settings == current_settings()) {
        if (result->ok) {
            apply_estimate(*result);
        } else if (!result->cancelled) {
            estimate_state_ = QStringLiteral("failed");
            emit estimateChanged();
        }
    } else if (!source_path_.isEmpty() && !estimate_job_ && !busy_) {
        schedule_estimate();
    }
}

void Backend::apply_estimate(const CompressResult &result) {
    const auto &report = result.report;
    estimate_state_ = QStringLiteral("ready");
    estimate_size_ = qs(engine::format_bytes(report.archive_size));
    estimate_times_.clear();
    static const std::pair<const char *, const char *> shown[] = {
        {"packet_1200", "Packet 1200"}, {"vara_hf", "VARA HF"}, {"vara_fm_narrow", "VARA FM"}};
    for (const auto &[id, label] : shown) {
        for (const auto &[mode, seconds] : report.estimates) {
            if (mode == id) {
                QVariantMap item;
                item[QStringLiteral("label")] = QString::fromUtf8(label);
                item[QStringLiteral("time")] = qs(engine::format_duration(seconds));
                estimate_times_.append(item);
            }
        }
    }
    estimate_over_limit_ = report.archive_size > engine::WINLINK_MAX_ATTACHMENT;
    emit estimateChanged();
}

void Backend::compress() {
    if (busy_ || source_path_.isEmpty())
        return;
    Settings settings = current_settings();
    say(QStringLiteral("Compression en qualité « %1 », format %2…")
            .arg(settings.quality, settings.compact ? QStringLiteral(".psdz") : QStringLiteral(".psdi")));
    // The estimate already made this very archive: it is the result.
    if (cached_settings_ && *cached_settings_ == settings && cached_result_) {
        deliver_archive(settings, *cached_result_);
        return;
    }
    set_busy(true, QStringLiteral("Compression"));
    if (estimate_job_ && estimate_job_->settings == settings) {
        // The estimate under way is this compression: the operator now
        // waits on it, at the priority of a foreground job.
        estimate_job_->adopted = true;
        foreground_job_ = estimate_job_;
        if (estimate_job_->thread)
            estimate_job_->thread->setPriority(QThread::NormalPriority);
        return;
    }
    if (estimate_job_)
        estimate_job_->cancel = true;
    estimate_timer_.stop();
    start_compression(settings, true);
}

void Backend::deliver_archive(const Settings &settings, const CompressResult &result) {
    const auto &info = result.report;
    QString base = QFileInfo(source_name_).completeBaseName();
    if (base.isEmpty())
        base = QStringLiteral("document");
    QString name = safe_name(base) + qs(info.extension);
    QString path = output_dir(QStringLiteral("archives")) + QLatin1Char('/') + name;
    QString error;
    if (!write_file(path, info.archive, &error)) {
        say(QStringLiteral("Écriture de l'archive impossible : %1").arg(error), QStringLiteral("error"));
        emit failed(QStringLiteral("Échec de l'écriture"), error);
        return;
    }
    archive_path_ = path;
    archive_name_ = name;

    if (info.auto_mode_switch) {
        if (*info.auto_mode_switch == "image")
            say(QStringLiteral("Source image : encodée en mode image de page."));
        else
            say(QStringLiteral("Bascule automatique en mode image de page (%1) — le document est scanné ou pivoté.")
                    .arg(reason_label(*info.auto_mode_switch)),
                QStringLiteral("warning"));
    }
    say(QStringLiteral("Archive écrite : %1 (%2 de l'original) en %3 s")
            .arg(qs(engine::format_bytes(info.archive_size)), qs(engine::format_percent(info.ratio_percent)),
                 decimal_seconds(info.compression_time)),
        QStringLiteral("success"));
    archive_note_.clear();
    if (info.compact) {
        archive_note_ = QStringLiteral("Archive compacte .psdz : la station qui la reçoit doit utiliser PDF "
                                       "Teleporter 2.5.0 ou plus ; la version F1GBD ne l'ouvre pas.");
        say(archive_note_, QStringLiteral("warning"));
    }
    QString times;
    for (const auto &[mode, seconds] : info.estimates) {
        if (mode == "packet_1200")
            times += QStringLiteral("Packet 1200 : %1").arg(qs(engine::format_duration(seconds)));
    }
    for (const auto &[mode, seconds] : info.estimates) {
        if (mode == "vara_hf")
            times += QStringLiteral(" · VARA HF : %1").arg(qs(engine::format_duration(seconds)));
    }
    for (const auto &[mode, seconds] : info.estimates) {
        if (mode == "vara_fm_narrow")
            times += QStringLiteral(" · VARA FM : %1").arg(qs(engine::format_duration(seconds)));
    }
    say(QStringLiteral("Sur l'air — %1").arg(times));
    if (info.archive_size > engine::WINLINK_MAX_ATTACHMENT) {
        QString warning = QStringLiteral("L'archive dépasse la limite de pièce jointe Winlink de %1 ko — choisissez "
                                         "une qualité inférieure.")
                              .arg(engine::WINLINK_MAX_ATTACHMENT / 1024);
        say(warning, QStringLiteral("warning"));
        if (archive_note_.isEmpty())
            archive_note_ = warning;
        else
            archive_note_ += QLatin1Char('\n') + warning;
    }
    archive_info_ = QStringLiteral("%1 · %2 de l'original · %3 s")
                        .arg(qs(engine::format_bytes(info.archive_size)),
                             qs(engine::format_percent(info.ratio_percent)), decimal_seconds(info.compression_time));
    Q_UNUSED(settings);
    emit archiveChanged();
}

// --- rebuild ------------------------------------------------------------------------------

void Backend::rebuild() {
    if (busy_ || received_path_.isEmpty() || !received_valid_)
        return;
    std::string data;
    QString error;
    if (!read_file(received_path_, &data, &error)) {
        say(QStringLiteral("Lecture de l'archive impossible : %1").arg(error), QStringLiteral("error"));
        return;
    }
    say(QStringLiteral("Recomposition du PDF…"));
    set_busy(true, QStringLiteral("Recomposition"));
    auto job = std::make_shared<Job>();
    job->adopted = true;
    foreground_job_ = job;
    QString base = QFileInfo(received_name_).completeBaseName();
    if (base.isEmpty())
        base = QStringLiteral("document");
    QString target = output_dir(QStringLiteral("pdf")) + QLatin1Char('/') + safe_name(base) + QStringLiteral("_rebuilt.pdf");

    QThread *thread = QThread::create([this, job, data = std::move(data), target]() {
        engine::ProgressFn progress = [this, job](int step, int total, const std::string &message) {
            double value = total > 0 ? static_cast<double>(step) / total : 0;
            QString text = qs(message);
            QMetaObject::invokeMethod(
                this, [this, job, value, text]() {
                    if (job == foreground_job_)
                        set_progress(value, text);
                },
                Qt::QueuedConnection);
        };
        bool ok = false, cancelled = false;
        QString error;
        engine::RebuildReport report;
        try {
            report = engine::archive_to_pdf(data, progress, &job->cancel);
            ok = write_file(target, report.pdf, &error);
        } catch (const engine::Cancelled &) {
            cancelled = true;
        } catch (const std::exception &e) {
            error = QString::fromUtf8(e.what());
        }
        QMetaObject::invokeMethod(
            this,
            [this, job, ok, cancelled, error, target, pages = report.pages, crc_ok = report.crc_ok,
             size = static_cast<long long>(report.pdf.size())]() {
                if (job == foreground_job_)
                    foreground_job_.reset();
                set_busy(false);
                if (cancelled) {
                    say(QStringLiteral("Recomposition annulée."), QStringLiteral("warning"));
                    return;
                }
                if (!ok) {
                    say(QStringLiteral("Échec de la recomposition : %1").arg(error), QStringLiteral("error"));
                    emit failed(QStringLiteral("Échec de la recomposition"), error);
                    return;
                }
                if (!crc_ok)
                    say(QStringLiteral("CRC du manifeste incorrect — le PDF recomposé peut être endommagé."),
                        QStringLiteral("warning"));
                pdf_path_ = target;
                pdf_name_ = QFileInfo(target).fileName();
                pdf_info_ = QStringLiteral("%1 page(s) · %2").arg(pages).arg(qs(engine::format_bytes(size)));
                say(QStringLiteral("PDF recomposé : %1 page(s), %2").arg(pages).arg(qs(engine::format_bytes(size))),
                    QStringLiteral("success"));
                emit pdfChanged();
            },
            Qt::QueuedConnection);
    });
    thread->setParent(this);
    thread->setStackSize(kEngineStackSize);
    connect(thread, &QThread::finished, thread, &QObject::deleteLater);
    job->thread = thread;
    thread->start();
}

void Backend::cancel() {
    if (foreground_job_)
        foreground_job_->cancel = true;
}

// --- handing results over ---------------------------------------------------------------

void Backend::copy_to(const QString &from, const QUrl &to, const QString &what) {
    std::string data;
    QString error;
    if (!read_file(from, &data, &error)) {
        say(QStringLiteral("Lecture impossible : %1").arg(error), QStringLiteral("error"));
        return;
    }
    QString target = file_name_of(to);
    QFile out(target);
    if (!out.open(QIODevice::WriteOnly | QIODevice::Truncate) ||
        out.write(data.data(), static_cast<qint64>(data.size())) != static_cast<qint64>(data.size())) {
        say(QStringLiteral("Enregistrement impossible : %1").arg(out.errorString()), QStringLiteral("error"));
        emit failed(QStringLiteral("Échec de l'enregistrement"), out.errorString());
        return;
    }
    out.close();
    QString shown = QFileInfo(target).fileName();
    say(QStringLiteral("%1 enregistré%2 : %3").arg(what, what.endsWith(QLatin1Char('e')) ? QStringLiteral("e") : QString(), shown),
        QStringLiteral("success"));
    emit notify(QStringLiteral("Enregistré : %1").arg(shown));
}

void Backend::shareArchive() {
    if (archive_path_.isEmpty())
        return;
    if (!platform::shareFile(archive_path_, QStringLiteral("application/octet-stream"),
                             QStringLiteral("Envoyer l'archive")))
        say(QStringLiteral("Aucune application ne peut recevoir l'archive."), QStringLiteral("warning"));
}

void Backend::saveArchiveTo(const QUrl &url) {
    if (!archive_path_.isEmpty())
        copy_to(archive_path_, url, QStringLiteral("Archive"));
}

void Backend::sharePdf() {
    if (pdf_path_.isEmpty())
        return;
    if (!platform::shareFile(pdf_path_, QStringLiteral("application/pdf"), QStringLiteral("Envoyer le PDF")))
        say(QStringLiteral("Aucune application ne peut recevoir le PDF."), QStringLiteral("warning"));
}

void Backend::viewPdf() {
    if (pdf_path_.isEmpty())
        return;
    if (!platform::openFile(pdf_path_, QStringLiteral("application/pdf")))
        say(QStringLiteral("Aucune application ne sait afficher un PDF sur cet appareil."), QStringLiteral("warning"));
}

void Backend::savePdfTo(const QUrl &url) {
    if (!pdf_path_.isEmpty())
        copy_to(pdf_path_, url, QStringLiteral("PDF"));
}

void Backend::copyLog() {
    if (QClipboard *clipboard = QGuiApplication::clipboard())
        clipboard->setText(log_.text());
    emit notify(QStringLiteral("Journal copié"));
}

QString Backend::licenseText(const QString &which) const {
    QString path = which == QStringLiteral("gpl") ? QStringLiteral(":/legal/LICENSE.txt")
                                                  : QStringLiteral(":/legal/THIRD_PARTY_NOTICES.txt");
    QFile in(path);
    if (!in.open(QIODevice::ReadOnly))
        return QStringLiteral("(texte absent de cette compilation)");
    return QString::fromUtf8(in.readAll());
}

// --- settings -------------------------------------------------------------------------------

void Backend::load_settings() {
    QSettings settings;
    theme_ = settings.value(QStringLiteral("ui/theme"), QStringLiteral("light")).toString();
    if (theme_ != QStringLiteral("light") && theme_ != QStringLiteral("dark") && theme_ != QStringLiteral("red"))
        theme_ = QStringLiteral("light");
    quality_index_ = kQualityIds.indexOf(settings.value(QStringLiteral("compress/quality"), QStringLiteral("medium")).toString());
    if (quality_index_ < 0)
        quality_index_ = 2;
    text_only_ = settings.value(QStringLiteral("compress/textOnly"), false).toBool();
    // Compatibility is chosen again at every start: an archive the receiving
    // station cannot open costs a second transmission.
    compatible_ = true;
}

void Backend::save_settings() const {
    QSettings settings;
    settings.setValue(QStringLiteral("ui/theme"), theme_);
    settings.setValue(QStringLiteral("compress/quality"), quality_id());
    settings.setValue(QStringLiteral("compress/textOnly"), text_only_);
}

} // namespace pdft::app
