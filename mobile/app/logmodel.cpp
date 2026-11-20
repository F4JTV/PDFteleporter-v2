// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only

#include "logmodel.h"

#include <QTime>

namespace pdft::app {

namespace {
// A log kept for a whole day of exercise stays readable; older lines go.
constexpr int kMaxEntries = 2000;
}

LogModel::LogModel(QObject *parent) : QAbstractListModel(parent) {}

int LogModel::rowCount(const QModelIndex &parent) const {
    return parent.isValid() ? 0 : static_cast<int>(entries_.size());
}

QVariant LogModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= entries_.size())
        return {};
    const Entry &e = entries_.at(index.row());
    switch (role) {
    case TimeRole:
        return e.time;
    case MessageRole:
    case Qt::DisplayRole:
        return e.message;
    case LevelRole:
        return e.level;
    default:
        return {};
    }
}

QHash<int, QByteArray> LogModel::roleNames() const {
    return {{TimeRole, "time"}, {MessageRole, "message"}, {LevelRole, "level"}};
}

void LogModel::append(const QString &message, const QString &level) {
    if (entries_.size() >= kMaxEntries) {
        beginRemoveRows(QModelIndex(), 0, 0);
        entries_.removeFirst();
        endRemoveRows();
    }
    const int row = static_cast<int>(entries_.size());
    beginInsertRows(QModelIndex(), row, row);
    entries_.append({QTime::currentTime().toString(QStringLiteral("HH:mm:ss")), message, level});
    endInsertRows();
    emit countChanged();
}

void LogModel::clear() {
    if (entries_.isEmpty())
        return;
    beginResetModel();
    entries_.clear();
    endResetModel();
    emit countChanged();
}

QString LogModel::text() const {
    QString out;
    for (const Entry &e : entries_)
        out += QStringLiteral("[%1] %2\n").arg(e.time, e.message);
    return out;
}

} // namespace pdft::app
