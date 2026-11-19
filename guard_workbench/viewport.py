"""Plan-view preview of the configured urban scene."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from .scene import SceneConfig


# Coordinates in meters relative to the target's start, ordered as north/east.
_ROUTE = ((0, 0), (16, 0), (16, 12), (0, 12))
_BUILDING_NORTH_EAST = (8, 6)
_BASE_FRAME = QRectF(-10, -24, 34, 32)
_ROUTE_LENGTH = sum(math.dist(start, end) for start, end in zip(_ROUTE, (*_ROUTE[1:], _ROUTE[0])))


def _point(north: float, east: float) -> QPointF:
    return QPointF(east, -north)


def _pen(color: str, width: float = 1.0) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCosmetic(True)
    return pen


def _target_at(scene: SceneConfig, elapsed_s: float) -> QPointF:
    route = _ROUTE
    if scene.target_route_direction == "reverse":
        route = (route[0], *reversed(route[1:]))
    segments = tuple(zip(route, (*route[1:], route[0])))
    distance = elapsed_s * scene.target_speed_mps % _ROUTE_LENGTH
    for start, end in segments:
        length = math.dist(start, end)
        if distance < length:
            ratio = distance / length
            return _point(start[0] + ratio * (end[0] - start[0]), start[1] + ratio * (end[1] - start[1]))
        distance -= length
    return _point(*route[0])


class SceneViewport(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self._drawing = QGraphicsScene(self)
        self.setScene(self._drawing)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(360)

    def show_scene(self, config: SceneConfig | None, elapsed_s: float = 0) -> None:
        drawing = self._drawing
        drawing.clear()
        drawing.setBackgroundBrush(QColor("#0d171c"))
        frame = QRectF(_BASE_FRAME)
        if config is not None:
            for obstacle in config.obstacles:
                center = _point(obstacle.north_m, obstacle.east_m)
                footprint = QRectF(center.x() - obstacle.width_m / 2, center.y() - obstacle.length_m / 2,
                                   obstacle.width_m, obstacle.length_m)
                frame = frame.united(footprint.adjusted(-4, -4, 4, 4))
                color = "#edb76d" if obstacle.is_active(elapsed_s) else "#6e5c49"
                drawing.addRect(footprint, _pen(color, 1.4), QColor("#4a382b")).setZValue(2)

            corners = [_point(*corner) for corner in _ROUTE]
            route = QPainterPath(corners[0])
            for point in corners[1:]:
                route.lineTo(point)
            route.closeSubpath()
            drawing.addPath(route, _pen("#253b42", 16), QColor("#142229"))
            route_pen = _pen("#52cabc", 2)
            route_pen.setStyle(Qt.PenStyle.DashLine)
            drawing.addPath(route, route_pen).setZValue(1)

            center = _point(*_BUILDING_NORTH_EAST)
            drawing.addRect(center.x() - 4.3, center.y() - 2.3, 8.6, 4.6,
                            _pen("#60757e", 2), QColor("#293941")).setZValue(1)
            for corner in corners:
                drawing.addEllipse(corner.x() - 0.20, corner.y() - 0.20, 0.4, 0.4,
                                   _pen("#52cabc"), QColor("#52cabc")).setZValue(1)
            target = _target_at(config, elapsed_s)
            drawing.addEllipse(target.x() - 0.36, target.y() - 0.36, 0.72, 0.72,
                               _pen("#bef9ee", 2), QColor("#48c7b5")).setZValue(3)

        # Keep grid line counts bounded for scenes with distant obstacles.
        step = max(2, math.ceil(max(frame.width(), frame.height()) / 36))
        grid_pen = _pen("#203037")
        for east in range(math.ceil(frame.left() / step) * step, math.floor(frame.right()) + 1, step):
            drawing.addLine(east, frame.top(), east, frame.bottom(), grid_pen).setZValue(-1)
        for screen_y in range(math.ceil(frame.top() / step) * step, math.floor(frame.bottom()) + 1, step):
            drawing.addLine(frame.left(), screen_y, frame.right(), screen_y, grid_pen).setZValue(-1)
        drawing.setSceneRect(frame)
        self.fitInView(frame, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fitInView(self._drawing.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
