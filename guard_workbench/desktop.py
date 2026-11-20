"""Native desktop entry point and project navigation."""

import argparse
from pathlib import Path
import sqlite3

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QSizePolicy, QSlider, QSplitter, QVBoxLayout, QWidget,
)

from .run_panel import RunPanel
from .scene_editor import SceneEditor
from .viewport import SceneViewport
from .workspace import Project, Workspace


TITLE = "无人机守卫仿真工作台"
_STYLE = """
QWidget { background: #141e24; color: #deebee; }
QLabel#title { font-size: 21px; font-weight: 700; }
QLabel#section { font-size: 14px; font-weight: 700; }
QLabel#muted { color: #97aeb5; }
QLabel#error { color: #ffc081; }
QFrame#toolbar { background: #1b292f; border-bottom: 1px solid #30434b; }
QFrame#toolbar QLabel { background: transparent; }
QPushButton { color: #e1eef0; background: #23343b; border: 1px solid #3b515b;
              border-radius: 5px; padding: 6px 11px; }
QPushButton:hover { background: #304650; }
QPushButton#primary { background: #4ac2b0; color: #10221f; font-weight: 700; }
QPushButton#primary:hover { background: #72d7c7; }
QPushButton:disabled, QPushButton#primary:disabled { color: #8aa3ab; background: #212d33; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget {
    background: #101a20; color: #deebee; border: 1px solid #344a54;
    border-radius: 4px; padding: 4px 6px; selection-background-color: #28584f;
}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus { border-color: #50bdaf; }
QListWidget::item { padding: 8px 5px; border-radius: 4px; }
QListWidget::item:selected { background: #28584f; color: #eefaf7; }
QTabWidget::pane { border: 1px solid #30434b; }
QTabBar::tab { background: #1a282f; color: #a6bcc2; padding: 9px 12px; }
QTabBar::tab:selected { background: #243942; color: #9fecde; border-bottom: 2px solid #4bc4b2; }
QGraphicsView { border: 1px solid #31464e; }
QSplitter::handle { background: #26373f; }
QSlider::groove:horizontal { height: 4px; background: #253740; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #41897e; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -4px 0; border-radius: 6px; background: #58c8b7; }
QSlider::handle:horizontal:disabled { background: #43565e; }
QStatusBar { background: #17262d; border-top: 1px solid #30434b; }
"""


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
        layout.addWidget(_label(TITLE, "title"))
        hint = "首次使用，请设置本地账号和 8–128 位密码。" if self.first_launch else "登录以打开本地项目。"
        layout.addWidget(_label(hint, "muted"))
        form = QFormLayout()
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
        self.setWindowTitle(TITLE)
        self.setMinimumSize(960, 650)
        self.resize(1260, 760)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(central)
        header = QFrame()
        header.setObjectName("toolbar")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(18, 9, 16, 9)
        bar.addWidget(_label(TITLE, "title"))
        bar.addStretch()
        self.account_label = _label(workspace.username or "", "muted")
        bar.addWidget(self.account_label)
        self.save_button = QPushButton("保存场景")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        bar.addWidget(self.save_button)
        exit_button = QPushButton("退出登录")
        exit_button.clicked.connect(self._sign_out)
        bar.addWidget(exit_button)
        layout.addWidget(header)

        panes = QSplitter(Qt.Orientation.Horizontal)
        panes.setChildrenCollapsible(False)
        layout.addWidget(panes, 1)
        projects = QWidget()
        projects.setMinimumWidth(185)
        left = QVBoxLayout(projects)
        left.addWidget(_label("项目", "section"))
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
        panes.addWidget(projects)

        preview = QWidget()
        middle = QVBoxLayout(preview)
        preview_header = QHBoxLayout()
        self.project_label = _label("新建或选择项目", "section")
        self.project_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        preview_header.addWidget(self.project_label, 1)
        preview_header.addWidget(_label("俯视示意  ·  北 ↑", "muted"))
        middle.addLayout(preview_header)
        self.viewport = SceneViewport()
        middle.addWidget(self.viewport, 1)
        middle.addWidget(_label("青色 · 目标路线和位置    灰色 · 建筑    橙色 · 当前障碍物", "muted"))
        timeline = QHBoxLayout()
        timeline.addWidget(_label("预览时间", "muted"))
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setAccessibleName("预览时间")
        self.time_slider.valueChanged.connect(self._refresh_preview)
        timeline.addWidget(self.time_slider, 1)
        self.time_label = _label("0.0 s", "muted")
        self.time_label.setMinimumWidth(65)
        timeline.addWidget(self.time_label)
        middle.addLayout(timeline)
        self.runs = RunPanel(workspace)
        self.runs.requested.connect(self._start_simulation)
        middle.addWidget(self.runs)
        panes.addWidget(preview)

        details = QWidget()
        right = QVBoxLayout(details)
        right.addWidget(_label("场景属性", "section"))
        self.editor = SceneEditor()
        self.editor.changed.connect(self._scene_changed)
        right.addWidget(self.editor, 1)
        panes.addWidget(details)
        panes.setStretchFactor(1, 1)
        panes.setSizes([220, 700, 325])

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
        self.editor.setEnabled(project is not None)
        self.rename_button.setEnabled(project is not None)
        self.save_button.setEnabled(project is not None)
        self.time_slider.setEnabled(project is not None)
        self.time_slider.blockSignals(True)
        self.time_slider.setValue(0)
        self.time_slider.blockSignals(False)
        self.project_label.setText(project.name if project else "新建或选择项目")
        self.project_label.setToolTip(project.name if project else "")
        if project is not None:
            self.editor.load_scene(project.scene)
        self._refresh_preview()
        self.runs.set_project(project.project_id if project else None)
        self.statusBar().showMessage("场景已加载" if project else "请选择或新建一个项目")

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
        self.time_label.setText(f"{elapsed:.1f} s")
        self.viewport.show_scene(config, elapsed)

    def _scene_changed(self) -> None:
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
            self.statusBar().showMessage("请等待仿真任务结束，或先取消运行")
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
            self.statusBar().showMessage("请等待仿真任务结束，或先取消运行")
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
    application.setFont(QFont("Microsoft YaHei UI", 10))
    application.setStyle("Fusion")
    application.setStyleSheet(_STYLE)
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
