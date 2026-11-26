"""Real AirSim camera display; empty states never substitute for simulator frames."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap, QResizeEvent
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class CameraView(QFrame):
    """Display the latest decoded RGB frame returned by an AirSim task."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("cameraPanel")
        self.setAccessibleName("AirSim 三维相机")
        self._pixmap = QPixmap()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        header = QLabel("AirSim 三维相机")
        header.setObjectName("section")
        layout.addWidget(header)
        self.details = QLabel("Workbench · RGB")
        self.details.setObjectName("muted")
        layout.addWidget(self.details)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setWordWrap(True)
        self.image.setMinimumSize(240, 180)
        self.image.setStyleSheet("background-color: #080A0C; border: 1px solid #2A2E35;")
        layout.addWidget(self.image, 1)
        self.timestamp = QLabel()
        self.timestamp.setObjectName("muted")
        self.timestamp.setFont(QFont("Cascadia Mono", 9))
        layout.addWidget(self.timestamp)
        self.clear()

    def clear(self, message: str = "运行 AirSim 任务后，在此显示真实三维相机画面。") -> None:
        self._pixmap = QPixmap()
        self.image.setPixmap(self._pixmap)
        self.image.setText(message)
        self.image.setStyleSheet(
            "background-color: #080A0C; border: 1px solid #2A2E35; color: #8B919A; padding: 16px;"
        )
        self.timestamp.clear()

    def show_frame(self, data: bytes, timestamp_ns: int | None = None) -> bool:
        pixmap = QPixmap()
        try:
            loaded = pixmap.loadFromData(data)
        except (TypeError, ValueError):
            loaded = False
        if not loaded:
            self.clear("AirSim 返回了无法解码的 RGB 图像。")
            return False
        self._pixmap = pixmap
        self.image.setText("")
        self.image.setStyleSheet("background-color: #080A0C; border: 1px solid #2A2E35;")
        self.timestamp.setText(
            f"图像时间戳  {timestamp_ns} ns" if timestamp_ns is not None else "实际 AirSim RGB 帧"
        )
        self._scale_frame()
        return True

    def _scale_frame(self) -> None:
        if not self._pixmap.isNull():
            self.image.setPixmap(self._pixmap.scaled(
                self.image.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._scale_frame()
