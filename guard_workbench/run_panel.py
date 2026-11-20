"""Qt task controls and persistent offline run history."""

from dataclasses import asdict
from datetime import datetime
import logging
import sqlite3
from threading import Event

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .airsim_backend import AIRSIM_BACKEND, AirSimEndpoint, run_airsim
from .offline import OFFLINE_BACKEND, SimulationCancelled, run_offline
from .result_dialog import ResultDialog
from .workspace import RunRecord, Workspace


_STATUSES = {"running": "未完成", "completed": "已完成", "failed": "失败", "cancelled": "已取消"}


def _local_time(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp).astimezone().strftime("%m-%d %H:%M:%S")


class _SimulationWorker(QThread):
    progress = Signal(int)

    def __init__(self, workspace: Workspace, run: RunRecord, endpoint: AirSimEndpoint | None,
                 parent: QWidget) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.record = run
        self.endpoint = endpoint
        self.stop_requested = Event()
        self.save_error: str | None = None

    def run(self) -> None:
        result = None
        callbacks = {"cancelled": self.stop_requested.is_set, "on_progress": self.progress.emit}
        try:
            if self.endpoint is None:
                result = asdict(run_offline(self.record.scene, **callbacks))
            else:
                result = run_airsim(self.record.scene, self.endpoint, **callbacks)
        except SimulationCancelled:
            status, message = "cancelled", "用户取消运行"
        except Exception as error:
            logging.getLogger(__name__).exception("Simulation run failed")
            status, message = "failed", str(error)
        else:
            status, message = "completed", "已保存运行时间序列"
        try:
            self.workspace.finish_run(self.record.info.run_id, status, message=message, result=result)
        except (ValueError, sqlite3.Error) as error:
            self.save_error = str(error)


class RunPanel(QWidget):
    requested = Signal()

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace
        self.project_id: str | None = None
        self.worker: _SimulationWorker | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        toolbar = QHBoxLayout()
        title = QLabel("仿真任务")
        title.setObjectName("section")
        toolbar.addWidget(title)
        self.backend = QComboBox()
        self.backend.addItem("离线估计", OFFLINE_BACKEND)
        self.backend.addItem("AirSim", AIRSIM_BACKEND)
        self.backend.setAccessibleName("仿真后端")
        toolbar.addWidget(self.backend)
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
        self.connection = QWidget()
        connection = QHBoxLayout(self.connection)
        connection.setContentsMargins(0, 0, 0, 0)
        connection.addWidget(QLabel("AirSim 本地端口"))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(41451)
        self.port.setAccessibleName("AirSim 本地端口")
        connection.addWidget(self.port)
        connection.addStretch()
        self.connection.hide()
        self.backend.currentIndexChanged.connect(self._show_connection)
        layout.addWidget(self.connection)
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
        backend = self.backend.currentData()
        try:
            endpoint = AirSimEndpoint(self.port.value()) if backend == AIRSIM_BACKEND else None
            record = self.workspace.start_run(self.project_id, backend)
        except (KeyError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "无法启动任务", str(error))
            return
        self.worker = _SimulationWorker(self.workspace, record, endpoint, self)
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
                    backend = "AirSim" if record.backend == AIRSIM_BACKEND else "离线估计"
                    item = QListWidgetItem(f"{_local_time(record.started_at)}   ·   {backend}   ·   {status}", self.history)
                    item.setData(Qt.ItemDataRole.UserRole, record.run_id)
                if records:
                    self.history.setCurrentRow(0)
        self._update_actions()

    def _show_connection(self) -> None:
        self.connection.setVisible(self.backend.currentData() == AIRSIM_BACKEND)

    def _update_actions(self) -> None:
        self.start_button.setEnabled(self.project_id is not None and not self.busy)
        self.backend.setEnabled(not self.busy)
        self.port.setEnabled(not self.busy)
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
        airsim = info.backend == AIRSIM_BACKEND
        if run.result is not None:
            summary = run.result["summary"]
            lines.append(f"时间样本：{summary['samples']}    已接收观测：{summary['received_observations']}")
            if airsim:
                lines.extend((f"相机可见比例：{summary['visibility_fraction']:.1%}",
                              f"平均相机距离：{summary['mean_camera_distance_m']:.2f} m"))
            else:
                lines.extend((f"位置 RMSE：{summary['rmse_m']:.3f} m",
                              f"95% 预测椭圆覆盖率：{summary['coverage_95']:.1%}"))
        if run.result is not None:
            try:
                dialog = ResultDialog(self.workspace, run, lines, self)
            except (ValueError, KeyError, sqlite3.Error) as error:
                QMessageBox.warning(self, "无法读取结果图表", str(error))
                return
            dialog.exec()
            dialog.deleteLater()
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("AirSim 仿真详情" if airsim else "离线仿真详情")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText("\n".join(lines))
        dialog.exec()
