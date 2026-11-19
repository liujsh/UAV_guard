"""Qt properties editor for the simulation scene."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .scene import Obstacle, SceneConfig


class _DecimalField(QDoubleSpinBox):
    def textFromValue(self, value: float) -> str:
        locale = self.locale()
        return super().textFromValue(value).rstrip(locale.zeroDigit()).rstrip(locale.decimalPoint())


def _decimal(minimum: float, maximum: float, unit: str = "", step: float = 0.1) -> QDoubleSpinBox:
    field = _DecimalField()
    field.setDecimals(8)
    field.setRange(minimum, maximum)
    field.setSingleStep(step)
    field.setSuffix(unit)
    field.setKeyboardTracking(False)
    return field


class _ObstacleDialog(QDialog):
    def __init__(self, obstacle: Obstacle, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("障碍物参数")
        self.obstacle = obstacle
        layout = QFormLayout(self)
        specs = (
            ("north_m", "北向位置", -1_000, 1_000, " m"),
            ("east_m", "东向位置", -1_000, 1_000, " m"),
            ("length_m", "北向长度", 0.05, 100, " m"),
            ("width_m", "东向宽度", 0.05, 100, " m"),
            ("height_m", "高度", 0.05, 100, " m"),
            ("start_s", "出现时间", 0, 3_600, " s"),
            ("end_s", "消失时间", 0, 3_600, " s"),
        )
        self.fields = {}
        for field, label, minimum, maximum, unit in specs:
            editor = _decimal(minimum, maximum, unit)
            editor.setValue(getattr(obstacle, field) or 0)
            layout.addRow(label, editor)
            self.fields[field] = editor
        self.keep_until_end = QCheckBox("持续到任务结束")
        self.keep_until_end.setChecked(obstacle.end_s is None)
        end = self.fields["end_s"]
        end.setEnabled(not self.keep_until_end.isChecked())
        self.keep_until_end.toggled.connect(end.setDisabled)
        layout.addRow(self.keep_until_end)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _validate(self) -> None:
        fields = {name: editor.value() for name, editor in self.fields.items()}
        if self.keep_until_end.isChecked():
            fields["end_s"] = None
        try:
            self.obstacle = Obstacle(**fields)
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()


class SceneEditor(QTabWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(290)
        self._obstacles: list[Obstacle] = []
        self._fields: dict[str, QDoubleSpinBox | QSpinBox] = {}
        scene = self._form_tab("场景")
        scene.addRow("地图模板", QLabel("城市遮挡"))
        self.seed = QLineEdit()
        self.seed.setMaxLength(10)
        self.seed.textChanged.connect(self._notify_changed)
        scene.addRow("随机种子", self.seed)
        self._add_number(scene, "duration_s", "任务时长", _decimal(1, 3_600, " s", 1))
        self._add_number(scene, "target_speed_mps", "目标速度", _decimal(0, 15, " m/s"))
        self.direction = QComboBox()
        self.direction.addItem("沿路线正向", "forward")
        self.direction.addItem("沿路线反向", "reverse")
        self.direction.currentIndexChanged.connect(self._notify_changed)
        scene.addRow("目标方向", self.direction)
        self._add_number(scene, "illumination_gain", "光照增益", _decimal(0.05, 1))
        for name, label, maximum in (("pedestrian_count", "行人数", 4), ("vehicle_count", "车辆数", 1)):
            count = QSpinBox()
            count.setRange(0, maximum)
            self._add_number(scene, name, label, count)

        uncertainty = self._form_tab("观测扰动")
        self._add_number(uncertainty, "measurement_noise_m", "位置噪声标准差", _decimal(0.01, 10, " m"))
        self._add_number(uncertainty, "observation_delay_s", "最大延迟", _decimal(0, 3_600, " s"))
        self._add_number(uncertainty, "observation_blackout_s", "遮挡持续时间", _decimal(0, 3_600, " s"))

        page = QWidget()
        obstacles = QVBoxLayout(page)
        commands = QHBoxLayout()
        self.add_obstacle = QPushButton("添加")
        self.edit_obstacle = QPushButton("编辑")
        self.remove_obstacle = QPushButton("移除")
        self.add_obstacle.clicked.connect(self._add_obstacle)
        self.edit_obstacle.clicked.connect(self._edit_obstacle)
        self.remove_obstacle.clicked.connect(self._remove_obstacle)
        for button in (self.add_obstacle, self.edit_obstacle, self.remove_obstacle):
            commands.addWidget(button)
        obstacles.addLayout(commands)
        self.obstacle_list = QListWidget()
        self.obstacle_list.currentRowChanged.connect(self._update_obstacle_actions)
        self.obstacle_list.itemDoubleClicked.connect(self._edit_obstacle)
        obstacles.addWidget(self.obstacle_list)
        self.addTab(page, "障碍物")
        self.load_scene(SceneConfig())

    def _form_tab(self, name: str) -> QFormLayout:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setContentsMargins(14, 16, 14, 14)
        form.setSpacing(10)
        self.addTab(tab, name)
        return form

    def _add_number(self, form: QFormLayout, name: str, label: str, editor: QDoubleSpinBox | QSpinBox) -> None:
        self._fields[name] = editor
        editor.valueChanged.connect(self._notify_changed)
        form.addRow(label, editor)

    def _notify_changed(self) -> None:
        self.changed.emit()

    def load_scene(self, scene: SceneConfig) -> None:
        blocked = self.blockSignals(True)
        try:
            self.seed.setText(str(scene.seed))
            self.direction.setCurrentIndex(self.direction.findData(scene.target_route_direction))
            for name, editor in self._fields.items():
                editor.setValue(getattr(scene, name))
            self._obstacles = list(scene.obstacles)
            self._refresh_obstacles()
        finally:
            self.blockSignals(blocked)

    def configuration(self) -> SceneConfig:
        seed = self.seed.text().strip()
        if not seed.isascii() or not seed.isdecimal():
            raise ValueError("随机种子必须是 0–4294967295 之间的整数")
        fields = {name: editor.value() for name, editor in self._fields.items()}
        return SceneConfig(seed=int(seed), target_route_direction=self.direction.currentData(),
                           obstacles=tuple(self._obstacles), **fields)

    def _refresh_obstacles(self, row: int = -1) -> None:
        self.obstacle_list.clear()
        for index, obstacle in enumerate(self._obstacles, 1):
            self.obstacle_list.addItem(f"障碍物 {index} · 北 {obstacle.north_m:g} / 东 {obstacle.east_m:g} m")
        self.obstacle_list.setCurrentRow(row)
        self._update_obstacle_actions()

    def _update_obstacle_actions(self) -> None:
        selected = self.obstacle_list.currentRow() >= 0
        self.add_obstacle.setEnabled(len(self._obstacles) < 32)
        self.edit_obstacle.setEnabled(selected)
        self.remove_obstacle.setEnabled(selected)

    def _add_obstacle(self) -> None:
        dialog = _ObstacleDialog(Obstacle(4, -2, 2, 1, 2), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._obstacles.append(dialog.obstacle)
            self._refresh_obstacles(len(self._obstacles) - 1)
            self.changed.emit()

    def _edit_obstacle(self) -> None:
        row = self.obstacle_list.currentRow()
        if row < 0:
            return
        dialog = _ObstacleDialog(self._obstacles[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._obstacles[row] = dialog.obstacle
            self._refresh_obstacles(row)
            self.changed.emit()

    def _remove_obstacle(self) -> None:
        row = self.obstacle_list.currentRow()
        if row >= 0:
            self._obstacles.pop(row)
            self._refresh_obstacles(min(row, len(self._obstacles) - 1))
            self.changed.emit()
