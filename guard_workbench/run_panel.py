"""Qt task controls and persistent offline run history."""

from dataclasses import asdict
from datetime import datetime
import logging
import sqlite3
from threading import Event

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from .offline import OFFLINE_BACKEND, OfflineResult, SimulationCancelled, run_offline
from .workspace import RunRecord, Workspace


_STATUSES = {"running": "未完成", "completed": "已完成", "failed": "失败", "cancelled": "已取消"}


def _local_time(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp).astimezone().strftime("%m-%d %H:%M:%S")


class _OfflineWorker(QThread):
    progress = Signal(int)

    def __init__(self, workspace: Workspace, run: RunRecord, parent: QWidget) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.record = run
        self.stop_requested = Event()
        self.save_error: str | None = None

    def run(self) -> None:
        result: OfflineResult | None = None
        try:
            result = run_offline(self.record.scene, cancelled=self.stop_requested.is_set,
                                 on_progress=self.progress.emit)
        except SimulationCancelled:
            status, message = "cancelled", "用户取消运行"
        except Exception as error:
            logging.getLogger(__name__).exception("Offline run failed")
            status, message = "failed", str(error)
        else:
            status, message = "completed", "已保存轨迹、误差和不确定性时间序列"
        try:
            self.workspace.finish_run(self.record.info.run_id, status, message=message,
                                      result=asdict(result) if result is not None else None)
        except (ValueError, sqlite3.Error) as error:
            self.save_error = str(error)


class RunPanel(QWidget):
    requested = Signal()

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace
        self.project_id: str | None = None
        self.worker: _OfflineWorker | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        toolbar = QHBoxLayout()
        title = QLabel("离线仿真")
        title.setObjectName("section")
        toolbar.addWidget(title)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("任务进度")
        self.progress.hide()
        toolbar.addWidget(self.progress, 1)
        toolbar.addStretch()
        self.start_button = QPushButton("运行")
        self.start_button.clicked.connect(self.requested)
        toolbar.addWidget(self.start_button)
        self.cancel_button = QPushButton("取消运行")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()
        toolbar.addWidget(self.cancel_button)
        self.details_button = QPushButton("运行详情")
        self.details_button.clicked.connect(self._show_details)
        toolbar.addWidget(self.details_button)
        layout.addLayout(toolbar)
        self.history = QListWidget()
        self.history.setAccessibleName("历史运行")
        self.history.setMaximumHeight(88)
        self.history.currentItemChanged.connect(self._update_actions)
        self.history.itemDoubleClicked.connect(self._show_details)
        layout.addWidget(self.history)
        self._update_actions()

    @property
    def busy(self) -> bool:
        return self.worker is not None

    def set_project(self, project_id: str | None) -> None:
        self.project_id = project_id
        self._reload_history()

    def start(self) -> None:
        if self.project_id is None or self.busy:
            return
        try:
            record = self.workspace.start_run(self.project_id, OFFLINE_BACKEND)
        except (KeyError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "无法启动任务", str(error))
            return
        self.worker = _OfflineWorker(self.workspace, record, self)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished.connect(self._finish)
        self.progress.reset()
        self.progress.show()
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self._reload_history()
        self.worker.start()

    def _cancel(self) -> None:
        if self.worker is not None:
            self.worker.stop_requested.set()
            self.cancel_button.setEnabled(False)

    def _finish(self) -> None:
        worker = self.worker
        if worker is None:
            return
        save_error = worker.save_error
        self.worker = None
        worker.deleteLater()
        self.progress.hide()
        self.cancel_button.hide()
        self._reload_history()
        if save_error is not None:
            QMessageBox.warning(self, "无法保存任务结果", save_error)

    def _reload_history(self) -> None:
        self.history.clear()
        if self.project_id is not None:
            try:
                records = self.workspace.list_runs(self.project_id)
            except sqlite3.Error as error:
                QMessageBox.warning(self, "无法读取运行历史", str(error))
            else:
                active = self.worker.record.info.run_id if self.worker is not None else None
                for record in records:
                    status = "运行中" if active == record.run_id else _STATUSES[record.status]
                    item = QListWidgetItem(f"{_local_time(record.started_at)}   ·   {status}", self.history)
                    item.setData(Qt.ItemDataRole.UserRole, record.run_id)
                if records:
                    self.history.setCurrentRow(0)
        self._update_actions()

    def _update_actions(self) -> None:
        self.start_button.setEnabled(self.project_id is not None and not self.busy)
        self.details_button.setEnabled(self.history.currentItem() is not None)

    def _show_details(self) -> None:
        selection = self.history.currentItem()
        if selection is None:
            return
        try:
            run = self.workspace.open_run(selection.data(Qt.ItemDataRole.UserRole))
        except (KeyError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "无法读取运行详情", str(error))
            return
        info = run.info
        status = "运行中" if self.worker and self.worker.record.info.run_id == info.run_id else _STATUSES[info.status]
        lines = [f"任务状态：{status}", f"开始：{_local_time(info.started_at)}",
                 f"随机种子：{run.scene.seed}    设定时长：{run.scene.duration_s:g} s"]
        lines.append(f"位置噪声：{run.scene.measurement_noise_m:g} m    延迟：{run.scene.observation_delay_s:g} s"
                     f"    遮挡：{run.scene.observation_blackout_s:g} s")
        if info.finished_at:
            lines.extend((f"结束：{_local_time(info.finished_at)}", info.message))
        if run.result is not None:
            summary = run.result["summary"]
            lines.extend((f"时间样本：{summary['samples']}    已接收观测：{summary['received_observations']}",
                          f"位置 RMSE：{summary['rmse_m']:.3f} m",
                          f"95% 预测椭圆覆盖率：{summary['coverage_95']:.1%}"))
        dialog = QMessageBox(self)
        dialog.setWindowTitle("离线仿真详情")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText("\n".join(lines))
        dialog.exec()
