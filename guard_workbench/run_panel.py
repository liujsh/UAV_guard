"""Task execution, AirSim preflight, run history, and persistent operation logs."""

from dataclasses import asdict
from datetime import datetime
import logging
import math
import sqlite3
from threading import Event

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .airsim_backend import AIRSIM_BACKEND, AirSimEndpoint, inspect_airsim, run_airsim
from .offline import OFFLINE_BACKEND, SimulationCancelled, run_offline
from .result_dialog import ResultDialog
from .workspace import RunRecord, Workspace


_STATUSES = {"running": "未完成", "completed": "已完成", "failed": "失败", "cancelled": "已取消"}


def _local_time(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp).astimezone().strftime("%m-%d %H:%M:%S")


class _SimulationWorker(QThread):
    progress = Signal(int)
    camera_frame = Signal(bytes, int)
    state = Signal(dict)

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
        if self.endpoint is not None:
            callbacks["on_camera_frame"] = self.camera_frame.emit
            callbacks["on_state"] = self.state.emit
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


class _PreflightWorker(QThread):
    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(self, endpoint: AirSimEndpoint, parent: QWidget) -> None:
        super().__init__(parent)
        self.endpoint = endpoint

    def run(self) -> None:
        try:
            result = inspect_airsim(self.endpoint)
        except Exception as error:
            logging.getLogger(__name__).exception("AirSim preflight failed")
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(result)


class RunPanel(QWidget):
    requested = Signal()
    camera_frame = Signal(bytes, int)
    run_state = Signal(dict)
    run_started = Signal(str)
    run_finished = Signal(str, str)

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace
        self.project_id: str | None = None
        self.worker: _SimulationWorker | None = None
        self.preflight: _PreflightWorker | None = None
        self._progress_mark = -1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.backend = QComboBox()
        self.backend.addItem("离线估计", OFFLINE_BACKEND)
        self.backend.addItem("AirSim", AIRSIM_BACKEND)
        self.backend.setAccessibleName("仿真后端")
        self.backend.setMaximumWidth(130)
        toolbar.addWidget(self.backend)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("任务进度")
        self.progress.hide()
        toolbar.addWidget(self.progress, 1)
        toolbar.addStretch()
        self.start_button = QPushButton("运行")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.requested)
        toolbar.addWidget(self.start_button)
        self.cancel_button = QPushButton("停止")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()
        toolbar.addWidget(self.cancel_button)
        self.details_button = QPushButton("结果分析")
        self.details_button.clicked.connect(self._show_details)
        toolbar.addWidget(self.details_button)
        layout.addLayout(toolbar)

        self.connection = QWidget()
        connection = QHBoxLayout(self.connection)
        connection.setContentsMargins(0, 0, 0, 0)
        connection.setSpacing(6)
        connection.addWidget(QLabel("本地端口"))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(41451)
        self.port.setAccessibleName("AirSim 本地端口")
        self.port.setMaximumWidth(100)
        connection.addWidget(self.port)
        self.check_button = QPushButton("测试连接")
        self.check_button.clicked.connect(self.test_connection)
        connection.addWidget(self.check_button)
        self.connection_state = QLabel("未测试")
        self.connection_state.setObjectName("muted")
        connection.addWidget(self.connection_state)
        connection.addStretch()
        layout.addWidget(self.connection)
        self.connection.hide()
        self.backend.currentIndexChanged.connect(self._show_connection)

        self.live_state = QLabel("未运行")
        self.live_state.setObjectName("muted")
        self.live_state.setWordWrap(True)
        self.live_state.setAccessibleName("实时守卫状态")
        layout.addWidget(self.live_state)

        self.pages = QTabWidget()
        self.history = QTableWidget(0, 4)
        self.history.setHorizontalHeaderLabels(["开始时间", "后端", "状态", "说明"])
        self.history.setAccessibleName("历史运行")
        self.history.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history.setAlternatingRowColors(True)
        self.history.verticalHeader().hide()
        self.history.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.history.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.history.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.history.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.history.currentItemChanged.connect(lambda *_: self._selection_changed())
        self.history.itemDoubleClicked.connect(lambda *_: self._show_details())
        self.pages.addTab(self.history, "运行历史")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Cascadia Mono", 9))
        self.log.setPlaceholderText("选择一条运行记录以查看任务日志。")
        self.pages.addTab(self.log, "任务日志")
        self.pages.setMaximumHeight(150)
        layout.addWidget(self.pages, 1)
        self._update_actions()

    @property
    def busy(self) -> bool:
        return self.worker is not None or self.preflight is not None

    def set_project(self, project_id: str | None) -> None:
        self.project_id = project_id
        self.log.clear()
        self._reload_history()

    def start(self) -> None:
        if self.project_id is None or self.busy:
            return
        backend = self.backend.currentData()
        record = None
        try:
            endpoint = AirSimEndpoint(self.port.value()) if backend == AIRSIM_BACKEND else None
            record = self.workspace.start_run(self.project_id, backend)
            location = f"127.0.0.1:{endpoint.port}" if endpoint is not None else "本机离线"
            self.workspace.append_run_log(record.info.run_id, f"任务创建；后端={backend}；位置={location}")
        except (KeyError, ValueError, OSError, sqlite3.Error) as error:
            if record is not None:
                self.workspace.finish_run(record.info.run_id, "failed", message=f"无法写入任务日志：{error}")
            QMessageBox.warning(self, "无法启动任务", str(error))
            return
        self.worker = _SimulationWorker(self.workspace, record, endpoint, self)
        self.worker.progress.connect(self._progress_changed)
        self.worker.camera_frame.connect(self.camera_frame.emit)
        self.worker.state.connect(self._state_changed)
        self.worker.finished.connect(self._finish)
        self.progress.reset()
        self.progress.show()
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.log.clear()
        self._progress_mark = -1
        self.live_state.setText("GuardDrone 正在起飞并校验初始位置…" if record.scene.guard_enabled
                                else "任务正在运行…")
        self.run_started.emit(record.info.run_id)
        self._reload_history()
        self.worker.start()

    def test_connection(self) -> None:
        if self.busy or self.backend.currentData() != AIRSIM_BACKEND:
            return
        try:
            endpoint = AirSimEndpoint(self.port.value())
        except ValueError as error:
            QMessageBox.warning(self, "无法测试连接", str(error))
            return
        self.preflight = _PreflightWorker(endpoint, self)
        self.preflight.succeeded.connect(self._preflight_succeeded)
        self.preflight.failed.connect(self._preflight_failed)
        self.preflight.finished.connect(self._preflight_finished)
        self._set_connection_state("正在检查 RPC、相机和场景资源…", "muted")
        self._update_actions()
        self.preflight.start()

    def _preflight_succeeded(self, result: dict) -> None:
        self._set_connection_state(f"可连接 · 服务端 v{result['server_version']}", "success")
        self.camera_frame.emit(result["frame_png"], result["image_timestamp_ns"])

    def _preflight_failed(self, message: str) -> None:
        self._set_connection_state("检查失败", "error")
        QMessageBox.warning(self, "AirSim 连接预检失败", message)

    def _preflight_finished(self) -> None:
        worker = self.preflight
        self.preflight = None
        if worker is not None:
            worker.deleteLater()
        self._update_actions()

    def _set_connection_state(self, text: str, appearance: str) -> None:
        self.connection_state.setText(text)
        self.connection_state.setObjectName(appearance)
        self.connection_state.style().unpolish(self.connection_state)
        self.connection_state.style().polish(self.connection_state)

    def _cancel(self) -> None:
        if self.worker is not None:
            self.worker.stop_requested.set()
            self.cancel_button.setEnabled(False)
            self.workspace.append_run_log(self.worker.record.info.run_id, "收到用户停止请求")

    def _state_changed(self, state: dict) -> None:
        if self.worker is None:
            return
        record = self.worker.record
        speed = math.hypot(state["applied_velocity_north_mps"], state["applied_velocity_east_mps"])
        guard = "守卫有效" if state["guard"] else ("失观" if not state["measurement_valid"] else "未满足距离带")
        text = (f"t={state['time_s']:.1f} s · 高度 {state['uav_height_m']:.1f} m · "
                f"距离 {state['guard_distance_m']:.2f} m · 速度 {speed:.2f} m/s · "
                f"风险 {state['predicted_state_risk']:.3f} · {guard}")
        if state.get("guard_event") == "observation_lost":
            text += " · 发生失观"
        elif state.get("guard_event") == "observation_reacquired":
            text += " · 观测恢复"
        self.live_state.setText(text)
        self.run_state.emit({**state, "project_id": record.info.project_id,
                             "run_id": record.info.run_id})

    def _progress_changed(self, percent: int) -> None:
        self.progress.setValue(percent)
        if self.worker is not None and percent // 25 > self._progress_mark:
            self._progress_mark = percent // 25
            self.workspace.append_run_log(self.worker.record.info.run_id, f"进度 {percent}%")
            item = self.history.item(self.history.currentRow(), 0)
            if item is not None and item.data(32) == self.worker.record.info.run_id:
                self.log.appendPlainText(f"进度 {percent}%")

    def _finish(self) -> None:
        worker = self.worker
        if worker is None:
            return
        run_id = worker.record.info.run_id
        save_error = worker.save_error
        stored_status = "failed"
        try:
            stored = self.workspace.open_run(run_id)
            stored_status = stored.info.status
            self.workspace.append_run_log(run_id, f"任务结束；状态={stored.info.status}；{stored.info.message}")
        except (OSError, KeyError, ValueError, sqlite3.Error) as error:
            save_error = str(error) if save_error is None else f"{save_error}；日志写入失败：{error}"
        self.worker = None
        worker.deleteLater()
        self.progress.hide()
        self.cancel_button.hide()
        self.live_state.setText(f"任务结束：{_STATUSES.get(stored_status, stored_status)}")
        self.run_finished.emit(run_id, stored_status)
        self._reload_history()
        if save_error is not None:
            QMessageBox.warning(self, "无法完整保存任务结果或日志", save_error)

    def _reload_history(self) -> None:
        selected = self.history.item(self.history.currentRow(), 0)
        selected_id = selected.data(32) if selected is not None else None
        self.history.blockSignals(True)
        self.history.setRowCount(0)
        if self.project_id is not None:
            try:
                records = self.workspace.list_runs(self.project_id)
            except sqlite3.Error as error:
                QMessageBox.warning(self, "无法读取运行历史", str(error))
            else:
                active = self.worker.record.info.run_id if self.worker is not None else None
                self.history.setRowCount(len(records))
                for row, record in enumerate(records):
                    status = "运行中" if active == record.run_id else _STATUSES[record.status]
                    backend = "AirSim" if record.backend == AIRSIM_BACKEND else "离线估计"
                    values = [_local_time(record.started_at), backend, status, record.message]
                    for column, value in enumerate(values):
                        item = QTableWidgetItem(value)
                        if column == 0:
                            item.setData(32, record.run_id)
                        item.setToolTip(value)
                        self.history.setItem(row, column, item)
                target_row = next((row for row, record in enumerate(records)
                                   if record.run_id == active or (active is None and record.run_id == selected_id)), 0)
                if records:
                    self.history.setCurrentCell(target_row, 0)
        self.history.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self) -> None:
        self._update_actions()
        item = self.history.item(self.history.currentRow(), 0)
        if item is None:
            return
        run_id = item.data(32)
        try:
            self.log.setPlainText(self.workspace.read_run_log(run_id))
        except (OSError, KeyError, ValueError, sqlite3.Error) as error:
            self.log.setPlainText(f"无法读取任务日志：{error}")

    def _show_connection(self) -> None:
        visible = self.backend.currentData() == AIRSIM_BACKEND
        self.connection.setVisible(visible)
        if not visible:
            self._set_connection_state("未测试", "muted")

    def _update_actions(self) -> None:
        can_run = self.project_id is not None and not self.busy
        self.start_button.setEnabled(can_run)
        self.backend.setEnabled(not self.busy)
        self.port.setEnabled(not self.busy)
        self.check_button.setEnabled(not self.busy)
        self.details_button.setEnabled(self.history.currentRow() >= 0)

    def _show_details(self) -> None:
        item = self.history.item(self.history.currentRow(), 0)
        if item is None:
            return
        try:
            run = self.workspace.open_run(item.data(32))
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
            if "rmse_m" in summary:
                lines.extend((f"位置 RMSE：{summary['rmse_m']:.3f} m",
                              f"95% 预测椭圆覆盖率：{summary['coverage_95']:.1%}"))
            if "estimator_version" in summary:
                lines.extend((f"估计器：{summary['estimator_version']}",
                              f"平均位置 NLL：{summary['mean_nll']:.4f}    "
                              f"OOSM 回放更新：{summary['oosm_updates']} 次"))
            if summary.get("guard_enabled"):
                lines.extend((f"控制器：{summary['controller_version']}    前瞻 q={summary['controller_q']:g}",
                              f"策略标识：{summary.get('guard_policy_id', 'geometric')}",
                              f"守卫时间比例：{summary['guard_fraction']:.1%}    "
                              f"平均守卫距离：{summary['mean_guard_distance_m']:.3f} m",
                              f"平均风险：遮挡 {summary['mean_predicted_occlusion_risk']:.4f}    "
                              f"距离 {summary['mean_predicted_distance_risk']:.4f}    "
                              f"综合 {summary['mean_predicted_state_risk']:.4f}",
                              f"平均控制代价：{summary['mean_control_effort']:.4f}    "
                              f"失观 {summary['observation_losses']} 次    "
                              f"重见 {summary['observation_reacquisitions']} 次"))
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
