"""Interactive charts and playback for completed runs."""

from base64 import b64decode
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
import sqlite3

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSlider, QTabWidget, QVBoxLayout, QWidget,
)

from .results import TRAJECTORY, Series, chart_data, export_csv, metrics
from .workspace import RunRecord, Workspace


class ResultChart(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(480, 280)
        self.series: list[Series] = []
        self.elapsed = 0.0
        self.trajectory = True
        self.x_label = self.y_label = ""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0c171d"))
        if not self.series:
            return
        bounds = QRectF(64, 38, self.width() - 91, self.height() - 90)
        xs = [x for series in self.series for x, _ in series.points]
        ys = [y for series in self.series for _, y in series.points]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        x_pad = (x_max - x_min) * .04 or 1
        y_pad = (y_max - y_min) * .04 or 1
        x_min, x_max = x_min - x_pad, x_max + x_pad
        y_min, y_max = y_min - y_pad, y_max + y_pad
        if self.trajectory:
            units_per_pixel = max((x_max - x_min) / bounds.width(), (y_max - y_min) / bounds.height())
            x_extra = (bounds.width() * units_per_pixel - (x_max - x_min)) / 2
            y_extra = (bounds.height() * units_per_pixel - (y_max - y_min)) / 2
            x_min, x_max = x_min - x_extra, x_max + x_extra
            y_min, y_max = y_min - y_extra, y_max + y_extra
        else:
            x_min, x_max = 0.0, max(xs)
            y_min = max(0.0, y_min)

        def point(x: float, y: float) -> QPointF:
            return QPointF(bounds.left() + (x - x_min) * bounds.width() / (x_max - x_min),
                           bounds.bottom() - (y - y_min) * bounds.height() / (y_max - y_min))

        painter.setPen(QColor("#9db4bb"))
        painter.drawText(QPointF(15, 23), self.y_label)
        painter.drawText(QPointF(bounds.right() - 72, self.height() - 12), self.x_label)
        for index in range(6):
            ratio = index / 5
            x = bounds.left() + ratio * bounds.width()
            y = bounds.bottom() - ratio * bounds.height()
            painter.setPen(QColor("#263b45"))
            painter.drawLine(QPointF(x, bounds.top()), QPointF(x, bounds.bottom()))
            painter.drawLine(QPointF(bounds.left(), y), QPointF(bounds.right(), y))
            painter.setPen(QColor("#9db4bb"))
            painter.drawText(QPointF(x - 12, bounds.bottom() + 19), f"{x_min + ratio * (x_max - x_min):.3g}")
            painter.drawText(QPointF(9, y + 4), f"{y_min + ratio * (y_max - y_min):.3g}")

        if not self.trajectory:
            cursor = point(self.elapsed, y_min).x()
            painter.setPen(QPen(QColor("#849da7"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(cursor, bounds.top()), QPointF(cursor, bounds.bottom()))

        legend_x = bounds.left() + 120
        for series in self.series:
            color = QColor(series.color)
            path = QPainterPath(point(*series.points[0]))
            for x, y in series.points[1:]:
                path.lineTo(point(x, y))
            painter.setPen(QPen(color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            sample = bisect_right(series.times, self.elapsed) - 1
            if 0 <= sample and self.elapsed <= series.times[-1]:
                painter.setBrush(color)
                painter.drawEllipse(point(*series.points[sample]), 4, 4)
            painter.drawText(QPointF(legend_x, 23), series.name)
            legend_x += painter.fontMetrics().horizontalAdvance(series.name) + 16


class ResultDialog(QDialog):
    def __init__(self, workspace: Workspace, run: RunRecord, lines: list[str], parent: QWidget) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.runs = [run]
        self.setWindowTitle("仿真结果")
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.selection = QComboBox()
        self.selection.addItem("单次运行", None)
        for item in workspace.list_runs(run.info.project_id):
            if item.run_id != run.info.run_id and item.backend == run.info.backend and item.status == "completed":
                stamp = datetime.fromisoformat(item.started_at).astimezone().strftime("%m-%d %H:%M:%S")
                self.selection.addItem(f"对照 {stamp} · {item.run_id[:8]}", item.run_id)
        controls.addWidget(self.selection, 1)
        self.metric_picker = QComboBox()
        self.metric_picker.addItems([TRAJECTORY, *metrics(run.info.backend)])
        controls.addWidget(self.metric_picker)
        export = QPushButton("导出 CSV")
        export.clicked.connect(self._export)
        controls.addWidget(export)
        layout.addLayout(controls)

        tabs = QTabWidget()
        chart_page = QWidget()
        chart_layout = QVBoxLayout(chart_page)
        self.comparison = QLabel()
        chart_layout.addWidget(self.comparison)
        self.chart = ResultChart()
        chart_layout.addWidget(self.chart, 1)
        timeline = QHBoxLayout()
        self.play = QPushButton("播放")
        self.play.clicked.connect(self._toggle_playback)
        timeline.addWidget(self.play)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName("回放时间")
        self.slider.valueChanged.connect(self._scrub)
        timeline.addWidget(self.slider, 1)
        self.time_label = QLabel()
        timeline.addWidget(self.time_label)
        chart_layout.addLayout(timeline)
        tabs.addTab(chart_page, "图表与回放")

        details = QWidget()
        detail_layout = QVBoxLayout(details)
        text = QLabel("\n".join(lines))
        text.setTextFormat(Qt.TextFormat.PlainText)
        detail_layout.addWidget(text)
        if frame := run.result.get("last_frame_png"):
            pixmap = QPixmap()
            if pixmap.loadFromData(b64decode(frame), "PNG"):
                image = QLabel()
                image.setPixmap(pixmap.scaled(640, 380, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
                detail_layout.addWidget(image)
        detail_layout.addStretch()
        tabs.addTab(details, "本次运行详情")
        layout.addWidget(tabs, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._advance)
        self.finished.connect(self.timer.stop)
        self.selection.currentIndexChanged.connect(self._compare)
        self.metric_picker.currentTextChanged.connect(self._refresh_chart)
        self._refresh_chart()

    def _compare(self) -> None:
        run_id = self.selection.currentData()
        self.runs = self.runs[:1]
        if run_id is not None:
            try:
                self.runs.append(self.workspace.open_run(run_id))
            except (KeyError, ValueError, sqlite3.Error) as error:
                QMessageBox.warning(self, "无法打开对照运行", str(error))
                self.selection.setCurrentIndex(0)
                return
        self._refresh_chart()

    def _refresh_chart(self) -> None:
        metric = self.metric_picker.currentText()
        self.chart.series, self.chart.x_label, self.chart.y_label = chart_data(self.runs, metric)
        self.chart.trajectory = metric == TRAJECTORY
        self.slider.setMaximum(round(max(series.times[-1] for series in self.chart.series) * 5))
        labels = []
        for name, run in zip(("本次", "对照"), self.runs):
            summary = run.result["summary"]
            value = (f"RMSE {summary['rmse_m']:.3f} m" if "rmse_m" in summary
                     else f"可见比例 {summary['visibility_fraction']:.1%}")
            labels.append(f"{name} · 种子 {run.scene.seed} · {summary['samples']} 个样本 · {value}")
        self.comparison.setText("    |    ".join(labels))
        self._scrub()

    def _scrub(self) -> None:
        self.chart.elapsed = self.slider.value() / 5
        self.time_label.setText(f"{self.chart.elapsed:.1f} s")
        self.chart.update()

    def _toggle_playback(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
            self.play.setText("播放")
            return
        if self.slider.value() == self.slider.maximum():
            self.slider.setValue(0)
        self.play.setText("暂停")
        self.timer.start()

    def _advance(self) -> None:
        self.slider.setValue(self.slider.value() + 1)
        if self.slider.value() == self.slider.maximum():
            self._toggle_playback()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出时间序列", "simulation-runs.csv", "CSV (*.csv)")
        if path:
            try:
                export_csv(Path(path), self.runs)
            except OSError as error:
                QMessageBox.warning(self, "无法导出 CSV", str(error))
