"""Interactive result charts, aligned run summaries, and raw-sample playback."""

from base64 import b64decode
from bisect import bisect_left
from datetime import datetime
from pathlib import Path
import sqlite3

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QSlider, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)
import pyqtgraph as pg

from .results import TRAJECTORY, Series, chart_data, export_csv, metrics
from .theme import BORDER, INSET, MUTED, TEXT
from .workspace import RunRecord, Workspace


pg.setConfigOptions(antialias=True)


def _nearest_index(times: tuple[float, ...], elapsed: float) -> int:
    position = bisect_left(times, elapsed)
    if position <= 0:
        return 0
    if position >= len(times):
        return len(times) - 1
    return position if abs(times[position] - elapsed) < abs(times[position - 1] - elapsed) else position - 1


def _read_only(item: QTableWidgetItem) -> QTableWidgetItem:
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


class ResultChart(QWidget):
    time_hovered = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(480, 280)
        self.series: list[Series] = []
        self.elapsed = 0.0
        self.trajectory = True
        self.x_label = self.y_label = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.readout = QLabel("移动指针或拖动时间轴读取样本值")
        self.readout.setObjectName("muted")
        self.readout.setFont(QFont("Cascadia Mono", 9))
        layout.addWidget(self.readout)
        self.plot = pg.PlotWidget(background=INSET)
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        for axis in ("left", "bottom"):
            self.plot.getAxis(axis).setPen(BORDER)
            self.plot.getAxis(axis).setTextPen(MUTED)
        self.legend = self.plot.addLegend(offset=(12, 8), labelTextColor=TEXT)
        self.cursor = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(TEXT, width=1, style=Qt.PenStyle.DashLine))
        self.cursor.hide()
        self.plot.addItem(self.cursor)
        layout.addWidget(self.plot, 1)
        self._mouse_proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=30, slot=self._hover)

    def refresh(self) -> None:
        self._redraw()
        super().update()

    def _redraw(self) -> None:
        self.plot.clear()
        self.legend.clear()
        self.cursor = pg.InfiniteLine(angle=90, movable=False,
                                      pen=pg.mkPen(TEXT, width=1, style=Qt.PenStyle.DashLine))
        self.plot.addItem(self.cursor)
        self.plot.setLabel("left", self.y_label, color=MUTED)
        self.plot.setLabel("bottom", self.x_label, color=MUTED)
        self.plot.setAspectLocked(self.trajectory, ratio=1.0)
        self.cursor.setVisible(not self.trajectory)
        for series in self.series:
            xs, ys = zip(*series.points)
            pen = pg.mkPen(series.color, width=2,
                           style=Qt.PenStyle.DashLine if series.dashed else Qt.PenStyle.SolidLine)
            self.plot.plot(xs, ys, pen=pen, name=series.name, connect="finite")
        if self.series and not self.trajectory:
            self.plot.setXRange(0, max(series.times[-1] for series in self.series), padding=0.02)
        else:
            self.plot.autoRange()
        self._refresh_cursor()

    def _refresh_cursor(self) -> None:
        if not self.trajectory:
            self.cursor.setPos(self.elapsed)
        if not self.series:
            self.readout.clear()
            return
        values = []
        for series in self.series:
            index = _nearest_index(series.times, self.elapsed)
            x, y = series.points[index]
            if self.trajectory:
                values.append(f"{series.name}  东 {x:.3f} m  北 {y:.3f} m")
            else:
                values.append(f"{series.name}  {y:.4g}")
        self.readout.setText(f"t = {self.elapsed:.1f} s    " + "    |    ".join(values))

    def _hover(self, event) -> None:
        if self.trajectory or not self.series:
            return
        position = event[0]
        if not self.plot.sceneBoundingRect().contains(position):
            return
        x = self.plot.plotItem.vb.mapSceneToView(position).x()
        times = self.series[0].times
        self.time_hovered.emit(times[_nearest_index(times, x)])


class ResultDialog(QDialog):
    def __init__(self, workspace: Workspace, run: RunRecord, lines: list[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.runs = [run]
        self.setWindowTitle("结果分析")
        self.resize(1100, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.selection = QComboBox()
        self.selection.addItem("单次运行", None)
        for item in workspace.list_runs(run.info.project_id):
            if item.run_id != run.info.run_id and item.backend == run.info.backend and item.status == "completed":
                stamp = datetime.fromisoformat(item.started_at).astimezone().strftime("%m-%d %H:%M:%S")
                self.selection.addItem(f"对照 {stamp} · {item.run_id[:8]}", item.run_id)
        controls.addWidget(self.selection, 1)
        self.metric_picker = QComboBox()
        self.metric_picker.addItems([TRAJECTORY, *metrics(run.info.backend)])
        self.metric_picker.setMinimumWidth(190)
        controls.addWidget(self.metric_picker)
        export = QPushButton("导出 CSV")
        export.clicked.connect(self._export)
        controls.addWidget(export)
        layout.addLayout(controls)

        self.summary_table = QTableWidget(0, 5)
        self.summary_table.setHorizontalHeaderLabels(["运行", "运行编号", "随机种子", "样本数", "关键指标"])
        self.summary_table.verticalHeader().hide()
        self.summary_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.summary_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.summary_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.summary_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.summary_table.setMaximumHeight(104)
        layout.addWidget(self.summary_table)

        tabs = QTabWidget()
        chart_page = QWidget()
        chart_layout = QVBoxLayout(chart_page)
        self.chart = ResultChart()
        chart_layout.addWidget(self.chart, 1)
        timeline = QHBoxLayout()
        self.play = QPushButton("播放")
        self.play.clicked.connect(self._toggle_playback)
        timeline.addWidget(self.play)
        step = QPushButton("步进")
        step.clicked.connect(lambda: self.slider.setValue(self.slider.value() + 1))
        timeline.addWidget(step)
        reset = QPushButton("复位")
        reset.clicked.connect(lambda: self.slider.setValue(0))
        timeline.addWidget(reset)
        self.speed = QComboBox()
        for label, value in (("0.5×", 400), ("1×", 200), ("2×", 100)):
            self.speed.addItem(label, value)
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(self._speed_changed)
        timeline.addWidget(self.speed)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName("回放时间")
        self.slider.valueChanged.connect(self._scrub)
        timeline.addWidget(self.slider, 1)
        self.time_label = QLabel()
        self.time_label.setFont(QFont("Cascadia Mono", 9))
        timeline.addWidget(self.time_label)
        chart_layout.addLayout(timeline)
        tabs.addTab(chart_page, "图表与回放")

        self.sample_table = QTableWidget(0, 0)
        self.sample_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sample_table.setAlternatingRowColors(True)
        self.sample_table.verticalHeader().hide()
        tabs.addTab(self.sample_table, "原始样本")

        details = QWidget()
        detail_layout = QVBoxLayout(details)
        text = QLabel("\n".join(lines))
        text.setTextFormat(Qt.TextFormat.PlainText)
        detail_layout.addWidget(text)
        if frame := run.result.get("last_frame_png"):
            pixmap = QPixmap()
            if pixmap.loadFromData(b64decode(frame)):
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
        self.chart.time_hovered.connect(lambda value: self.slider.setValue(round(value * 5)))
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
        self.summary_table.setRowCount(len(self.runs))
        for row, run in enumerate(self.runs):
            summary = run.result["summary"]
            value = (f"RMSE {summary['rmse_m']:.3f} m" if "rmse_m" in summary
                     else f"可见比例 {summary['visibility_fraction']:.1%}")
            cells = ["本次" if row == 0 else "对照", run.info.run_id[:8], str(run.scene.seed),
                     str(summary["samples"]), value]
            for column, text in enumerate(cells):
                item = _read_only(QTableWidgetItem(text))
                if column == 1:
                    item.setToolTip(run.info.run_id)
                self.summary_table.setItem(row, column, item)
        self._refresh_samples()
        self._scrub()

    def _refresh_samples(self) -> None:
        fields = list(dict.fromkeys(key for run in self.runs for row in run.result["samples"] for key in row))
        rows = [(run, sample) for run in self.runs for sample in run.result["samples"]]
        self.sample_table.setColumnCount(len(fields) + 1)
        self.sample_table.setHorizontalHeaderLabels(["运行编号", *fields])
        self.sample_table.setRowCount(len(rows))
        for row_index, (run, sample) in enumerate(rows):
            values = [run.info.run_id[:8], *("—" if sample.get(field) is None else sample.get(field) for field in fields)]
            for column, value in enumerate(values):
                text = str(int(value)) if isinstance(value, bool) else str(value)
                item = _read_only(QTableWidgetItem(text))
                if column == 0:
                    item.setToolTip(run.info.run_id)
                self.sample_table.setItem(row_index, column, item)

    def _scrub(self) -> None:
        self.chart.elapsed = self.slider.value() / 5
        self.time_label.setText(f"{self.chart.elapsed:.1f} s / {self.slider.maximum() / 5:.1f} s")
        self.chart.refresh()

    def _toggle_playback(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
            self.play.setText("播放")
            return
        if self.slider.value() == self.slider.maximum():
            self.slider.setValue(0)
        self.play.setText("暂停")
        self.timer.start()

    def _speed_changed(self) -> None:
        self.timer.setInterval(self.speed.currentData())

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
                QMessageBox.critical(self, "无法导出 CSV", str(error))
