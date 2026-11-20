// Copyright (C) 2026 The PDF Teleporter contributors
// SPDX-License-Identifier: GPL-3.0-only
//
// The operating log: timestamped lines, as the desktop version keeps them,
// to be read back during an exercise debrief.
#pragma once

#include <QAbstractListModel>
#include <QString>
#include <QVector>

namespace pdft::app {

class LogModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ count NOTIFY countChanged)

public:
    enum Role { TimeRole = Qt::UserRole + 1, MessageRole, LevelRole };

    explicit LogModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = QModelIndex()) const override;
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;

    int count() const { return static_cast<int>(entries_.size()); }

    // level: "info", "success", "warning" or "error"
    void append(const QString &message, const QString &level);
    Q_INVOKABLE void clear();
    // The whole log as plain text, one line an entry.
    Q_INVOKABLE QString text() const;

signals:
    void countChanged();

private:
    struct Entry {
        QString time;
        QString message;
        QString level;
    };
    QVector<Entry> entries_;
};

} // namespace pdft::app
