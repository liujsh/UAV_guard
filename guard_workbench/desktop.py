"""Native desktop entry point and dock-based research workspace."""

import argparse
from pathlib import Path
import sqlite3

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QDockWidget, QFormLayout, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSlider, QSplitter,
    QToolBar, QVBoxLayout, QWidget,
)

from .camera_view import CameraView
from .run_panel import RunPanel
from .scene_editor import SceneEditor
from .theme import apply_theme
from .viewport import SceneViewport
from .workspace import Project, Workspace


TITLE = "无人机守卫仿真工作台"


def _label(text: str, appearance: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setObjectName(appearance)
    return label


class _LoginDialog(QDialog):
    def __init__(self, workspace: Workspace, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.first_launch = not workspace.has_account()
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(410)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        layout.addWidget(_label(TITLE, "title"))
        hint = "首次使用，请设置本地账号和 8–128 位密码。" if self.first_launch else "登录以打开本地项目。"
        layout.addWidget(_label(hint, "muted"))
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.user = QLineEdit()
        self.user.setMaxLength(40)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMaxLength(128)
        self.confirmation = QLineEdit()
        self.confirmation.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirmation.setMaxLength(128)
        form.addRow("用户名", self.user)
        form.addRow("密码", self.password)
        if self.first_launch:
            form.addRow("确认密码", self.confirmation)
        layout.addLayout(form)
        self.error = _label("", "error")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        submit = buttons.button(QDialogButtonBox.StandardButton.Ok)
        submit.setText("创建并登录" if self.first_launch else "登录")
        submit.setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("退出")
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _submit(self) -> None:
        try:
            if self.first_launch:
                if self.password.text() != self.confirmation.text():
                    self.error.setText("两次密码输入不一致。")
                    return
                self.workspace.set_up_account(self.user.text(), self.password.text())
            elif not self.workspace.sign_in(self.user.text(), self.password.text()):
                self.error.setText("用户名或密码不正确。")
                return
        except (ValueError, sqlite3.Error) as error:
            self.error.setText(str(error))
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace
        self.project: Project | None = None
        self._pending_changes = False
        self._running_project_id: str | None = None
        self._live_run_state: dict | None = None
        self.setWindowTitle(TITLE)
        self.setMinimumSize(1100, 700)
        self.resize(1440, 860)
        self.setDockNestingEnabled(True)
        self.setAnimated(False)

        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.addToolBar(toolbar)
        toolbar.addWidget(_label(TITLE, "title"))
        toolbar.addSeparator()
        self.save_button = QPushButton("保存场景")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        toolbar.addWidget(self.save_button)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        toolbar.addWidget(spacer)
        self.account_label = _label(workspace.username or "", "muted")
        toolbar.addWidget(self.account_label)
        exit_button = QPushButton("退出登录")
        exit_button.clicked.connect(self._sign_out)
        toolbar.addWidget(exit_button)

        projects = QWidget()
        left = QVBoxLayout(projects)
        left.setContentsMargins(8, 8, 8, 8)
        left.setSpacing(8)
        self.projects = QListWidget()
        self.projects.setAccessibleName("项目列表")
        self.projects.currentItemChanged.connect(self._select_project)
        left.addWidget(self.projects, 1)
        actions = QHBoxLayout()
        create = QPushButton("新建")
        create.clicked.connect(self._create_project)
        self.rename_button = QPushButton("重命名")
        self.rename_button.clicked.connect(self._rename_project)
        actions.addWidget(create)
        actions.addWidget(self.rename_button)
        left.addLayout(actions)
        self.project_dock = QDockWidget("项目导航", self)
        self.project_dock.setObjectName("projectDock")
        self.project_dock.setWidget(projects)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)

        central = QWidget()
        middle = QVBoxLayout(central)
        middle.setContentsMargins(12, 10, 12, 10)
        middle.setSpacing(8)
        preview_header = QHBoxLayout()
        self.project_label = _label("新建或选择项目", "section")
        self.project_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        preview_header.addWidget(self.project_label, 1)
        preview_header.addWidget(_label("二维任务视图  ·  北 ↑", "muted"))
        middle.addLayout(preview_header)

        views = QSplitter(Qt.Orientation.Horizontal)
        views.setChildrenCollapsible(False)
        scene_panel = QWidget()
        scene_layout = QVBoxLayout(scene_panel)
        scene_layout.setContentsMargins(0, 0, 0, 0)
        scene_layout.setSpacing(6)
        self.viewport = SceneViewport()
        scene_layout.addWidget(self.viewport, 1)
        legend = _label("目标路线 / 当前目标    建筑    生效障碍物", "muted")
        scene_layout.addWidget(legend)
        timeline = QHBoxLayout()
        self.play_preview = QPushButton("播放")
        self.play_preview.clicked.connect(self._toggle_preview)
        timeline.addWidget(self.play_preview)
        step_preview = QPushButton("步进")
        step_preview.clicked.connect(lambda: self.time_slider.setValue(self.time_slider.value() + 1))
        timeline.addWidget(step_preview)
        reset_preview = QPushButton("复位")
        reset_preview.clicked.connect(lambda: self.time_slider.setValue(0))
        timeline.addWidget(reset_preview)
        self._preview_controls = (self.play_preview, step_preview, reset_preview)
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(self._advance_preview)
        timeline.addWidget(_label("预览时间", "muted"))
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setAccessibleName("预览时间")
        self.time_slider.valueChanged.connect(self._refresh_preview)
        timeline.addWidget(self.time_slider, 1)
        self.time_label = _label("0.0 s", "muted")
        self.time_label.setMinimumWidth(65)
        timeline.addWidget(self.time_label)
        scene_layout.addLayout(timeline)
        views.addWidget(scene_panel)
        self.camera = CameraView()
        views.addWidget(self.camera)
        views.setStretchFactor(0, 3)
        views.setStretchFactor(1, 2)
        views.setSizes([600, 400])
        middle.addWidget(views, 1)
        self.setCentralWidget(central)

        self.editor = SceneEditor()
        self.editor.changed.connect(self._scene_changed)
        self.editor_dock = QDockWidget("场景属性", self)
        self.editor_dock.setObjectName("editorDock")
        self.editor_dock.setWidget(self.editor)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.editor_dock)

        self.runs = RunPanel(workspace)
        self.runs.requested.connect(self._start_simulation)
        self.runs.camera_frame.connect(self.camera.show_frame)
        self.runs.run_started.connect(self._run_started)
        self.runs.run_finished.connect(self._run_finished)
        self.runs.run_state.connect(self._run_state_changed)
        self.runs.backend.currentTextChanged.connect(self._backend_changed)
        self.run_dock = QDockWidget("任务与日志", self)
        self.run_dock.setObjectName("runDock")
        self.run_dock.setWidget(self.runs)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.run_dock)
        self.resizeDocks([self.project_dock, self.editor_dock], [220, 310], Qt.Orientation.Horizontal)
        self.resizeDocks([self.run_dock], [230], Qt.Orientation.Vertical)

        self.backend_status = _label("离线估计", "muted")
        self.statusBar().addPermanentWidget(self.backend_status)
        self.statusBar().addPermanentWidget(_label("本地工作区", "muted"))
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save)
        self._set_project(None)
        self._reload_projects()

    def _reload_projects(self) -> None:
        self.projects.blockSignals(True)
        try:
            self.projects.clear()
            for project in self.workspace.list_projects():
                item = QListWidgetItem(project.name, self.projects)
                item.setData(Qt.ItemDataRole.UserRole, project.project_id)
                item.setToolTip(project.name)
            self._sync_selection()
        finally:
            self.projects.blockSignals(False)

    def _sync_selection(self) -> None:
        previous = self.projects.blockSignals(True)
        row = -1
        if self.project is not None:
            for index in range(self.projects.count()):
                if self.projects.item(index).data(Qt.ItemDataRole.UserRole) == self.project.project_id:
                    row = index
                    break
        self.projects.setCurrentRow(row)
        self.projects.blockSignals(previous)

    def _set_project(self, project: Project | None) -> None:
        self.project = project
        self._pending_changes = False
        self.preview_timer.stop()
        self.play_preview.setText("播放")
        running = project is not None and project.project_id == self._running_project_id
        for control in self._preview_controls:
            control.setEnabled(project is not None and not running)
        editable = project is not None and not running
        self.editor.setEnabled(editable)
        self.rename_button.setEnabled(project is not None)
        self.save_button.setEnabled(editable)
        self.time_slider.setEnabled(project is not None and not running)
        self.time_slider.blockSignals(True)
        self.time_slider.setValue(0)
        self.time_slider.blockSignals(False)
        self.project_label.setText(project.name if project else "新建或选择项目")
        self.project_label.setToolTip(project.name if project else "")
        if project is not None:
            self.editor.load_scene(project.scene)
        self.camera.clear()
        self._refresh_preview()
        self.runs.set_project(project.project_id if project else None)
        self.statusBar().showMessage("场景已加载" if project else "请选择或新建一个项目")

    def _toggle_preview(self) -> None:
        if self.project is None:
            return
        if self.preview_timer.isActive():
            self.preview_timer.stop()
            self.play_preview.setText("播放")
            return
        if self.time_slider.value() == self.time_slider.maximum():
            self.time_slider.setValue(0)
        self.play_preview.setText("暂停")
        self.preview_timer.start()

    def _advance_preview(self) -> None:
        self.time_slider.setValue(self.time_slider.value() + 1)
        if self.time_slider.value() == self.time_slider.maximum():
            self.preview_timer.stop()
            self.play_preview.setText("播放")

    def _refresh_preview(self) -> None:
        if self.project is None:
            self.viewport.show_scene(None)
            self.time_label.setText("0.0 s")
            return
        try:
            config = self.editor.configuration()
        except ValueError as error:
            self.statusBar().showMessage(str(error))
            return
        self.time_slider.blockSignals(True)
        self.time_slider.setMaximum(round(config.duration_s * 10))
        self.time_slider.blockSignals(False)
        elapsed = min(self.time_slider.value() / 10, config.duration_s)
        self.time_label.setText(f"{elapsed:.1f} s / {config.duration_s:g} s")
        live = (self._live_run_state if self.project.project_id == self._running_project_id else None)
        self.viewport.show_scene(config, elapsed, live)

    def _scene_changed(self) -> None:
        if self.project is None:
            return
        self._pending_changes = True
        self.project_label.setText(f"{self.project.name}  *")
        self.statusBar().showMessage("有未保存的场景修改")
        self._refresh_preview()

    def save(self) -> bool:
        if self.project is None:
            return True
        try:
            config = self.editor.configuration()
            project_id = self.project.project_id
            self.workspace.save_project_scene(project_id, config)
        except (ValueError, KeyError, sqlite3.Error) as error:
            QMessageBox.warning(self, "无法保存场景", str(error))
            return False
        self.project = Project(project_id, self.project.name, config)
        self._pending_changes = False
        self.project_label.setText(self.project.name)
        self.statusBar().showMessage("场景已保存")
        return True

    def _start_simulation(self) -> None:
        if self.project is not None and (not self._pending_changes or self.save()):
            self.runs.start()

    def _run_started(self, _run_id: str) -> None:
        self._running_project_id = self.project.project_id if self.project is not None else None
        self._live_run_state = None
        self.preview_timer.stop()
        self.play_preview.setText("播放")
        for control in self._preview_controls:
            control.setEnabled(False)
        self.time_slider.setEnabled(False)
        self.editor.setEnabled(False)
        self.save_button.setEnabled(False)
        if self.runs.backend.currentText() == "AirSim":
            self.camera.clear("AirSim 任务已启动，正在等待真实相机帧…")
        self.statusBar().showMessage("任务运行中；运行项目的场景配置和预览控制已锁定")

    def _run_state_changed(self, state: dict) -> None:
        if state.get("project_id") != self._running_project_id:
            return
        self._live_run_state = state
        if self.project is None or self.project.project_id != self._running_project_id:
            return
        elapsed = min(float(state["time_s"]), self.project.scene.duration_s)
        self.time_slider.blockSignals(True)
        self.time_slider.setValue(round(elapsed * 10))
        self.time_slider.blockSignals(False)
        self.time_label.setText(f"{elapsed:.1f} s / {self.project.scene.duration_s:g} s")
        self.viewport.show_scene(self.project.scene, elapsed, state)

    def _run_finished(self, _run_id: str, status: str) -> None:
        self._running_project_id = None
        self._live_run_state = None
        editable = self.project is not None
        self.editor.setEnabled(editable)
        self.save_button.setEnabled(editable)
        self.time_slider.setEnabled(editable)
        for control in self._preview_controls:
            control.setEnabled(editable)
        self._refresh_preview()
        self.statusBar().showMessage(f"任务状态：{status}")

    def _backend_changed(self, backend: str) -> None:
        self.backend_status.setText(backend)
        if backend != "AirSim" and not self.runs.busy:
            self.camera.clear()

    def _allow_leave(self) -> bool:
        if not self._pending_changes:
            return True
        prompt = QMessageBox(self)
        prompt.setWindowTitle("未保存的修改")
        prompt.setText("是否保存当前场景的修改？")
        save = prompt.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard = prompt.addButton("放弃修改", QMessageBox.ButtonRole.DestructiveRole)
        prompt.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(save)
        prompt.exec()
        if prompt.clickedButton() is save:
            return self.save()
        return prompt.clickedButton() is discard

    def _select_project(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        project_id = item.data(Qt.ItemDataRole.UserRole)
        if self.project is not None and project_id == self.project.project_id:
            return
        if not self._allow_leave():
            self._sync_selection()
            return
        try:
            self._set_project(self.workspace.open_project(project_id))
        except (ValueError, KeyError, sqlite3.Error) as error:
            self._sync_selection()
            QMessageBox.warning(self, "无法打开项目", str(error))

    def _create_project(self) -> None:
        name, accepted = QInputDialog.getText(self, "新建项目", "项目名称")
        if not accepted or not self._allow_leave():
            return
        try:
            self._set_project(self.workspace.create_project(name))
            self._reload_projects()
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "无法新建项目", str(error))

    def _rename_project(self) -> None:
        if self.project is None:
            return
        name, accepted = QInputDialog.getText(self, "重命名项目", "项目名称", text=self.project.name)
        if not accepted:
            return
        try:
            project_id = self.project.project_id
            self.workspace.rename_project(project_id, name)
            self.project = Project(project_id, name.strip(), self.project.scene)
            self.project_label.setText(self.project.name + ("  *" if self._pending_changes else ""))
            self.project_label.setToolTip(self.project.name)
            self._reload_projects()
        except (ValueError, KeyError, sqlite3.Error) as error:
            QMessageBox.warning(self, "无法重命名项目", str(error))

    def _sign_out(self) -> None:
        if self.runs.busy:
            self.statusBar().showMessage("请等待仿真或连接检查结束，或先停止运行")
            return
        if not self._allow_leave():
            return
        self.workspace.sign_out()
        self._set_project(None)
        self.hide()
        if _LoginDialog(self.workspace, self).exec() == QDialog.DialogCode.Accepted:
            self.account_label.setText(self.workspace.username or "")
            self._reload_projects()
            self.show()
        else:
            self.close()
            QApplication.quit()

    def closeEvent(self, event) -> None:
        if self.runs.busy:
            self.statusBar().showMessage("请等待仿真或连接检查结束，或先停止运行")
            event.ignore()
        elif self._allow_leave():
            event.accept()
        else:
            event.ignore()


def main() -> int:
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--workspace", type=Path, help="本地工作区数据库路径")
    args = parser.parse_args()
    application = QApplication([])
    application.setApplicationName(TITLE)
    application.setOrganizationName("UAV Guard")
    apply_theme(application)
    app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    path = args.workspace if args.workspace is not None else Path(app_data) / "workspace.sqlite3"
    try:
        workspace = Workspace(path)
        result = _LoginDialog(workspace).exec()
        if result != QDialog.DialogCode.Accepted:
            return 0
        window = MainWindow(workspace)
    except (OSError, ValueError, sqlite3.Error) as error:
        QMessageBox.critical(None, "无法打开工作区", str(error))
        return 1
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
