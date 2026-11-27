"""Plan-view preview of the configured urban scene."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from .route import URBAN_ROUTE, target_position
from .scene import SceneConfig


# Coordinates in meters relative to the target's start, ordered as north/east.
_BUILDING_NORTH_EAST = (8, 6)
_BASE_FRAME = QRectF(-10, -24, 34, 32)


def _point(north: float, east: float) -> QPointF:
    return QPointF(east, -north)


def _pen(color: str, width: float = 1.0) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCosmetic(True)
    return pen


class SceneViewport(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self._drawing = QGraphicsScene(self)
        self.setScene(self._drawing)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(360)

    def show_scene(self, config: SceneConfig | None, elapsed_s: float = 0,
                   run_state: dict | None = None) -> None:
        drawing = self._drawing
        drawing.clear()
        drawing.setBackgroundBrush(QColor("#0E1013"))
        frame = QRectF(_BASE_FRAME)
        if config is not None:
            for obstacle in config.obstacles:
                center = _point(obstacle.north_m, obstacle.east_m)
                footprint = QRectF(center.x() - obstacle.width_m / 2, center.y() - obstacle.length_m / 2,
                                   obstacle.width_m, obstacle.length_m)
                frame = frame.united(footprint.adjusted(-4, -4, 4, 4))
                color = "#E76624" if obstacle.is_active(elapsed_s) else "#62564A"
                drawing.addRect(footprint, _pen(color, 1.4), QColor("#2D2722")).setZValue(2)

            corners = [_point(*corner) for corner in URBAN_ROUTE]
            route = QPainterPath(corners[0])
            for point in corners[1:]:
                route.lineTo(point)
            route.closeSubpath()
            drawing.addPath(route, _pen("#242B33", 16), QColor("#171C22"))
            route_pen = _pen("#338CED", 2)
            route_pen.setStyle(Qt.PenStyle.DashLine)
            drawing.addPath(route, route_pen).setZValue(1)

            center = _point(*_BUILDING_NORTH_EAST)
            drawing.addRect(center.x() - 4.3, center.y() - 2.3, 8.6, 4.6,
                            _pen("#65717D", 2), QColor("#272D35")).setZValue(1)
            for corner in corners:
                drawing.addEllipse(corner.x() - 0.20, corner.y() - 0.20, 0.4, 0.4,
                                   _pen("#338CED"), QColor("#338CED")).setZValue(1)
            target = _point(*target_position(elapsed_s * config.target_speed_mps, config.target_route_direction))
            drawing.addEllipse(target.x() - 0.36, target.y() - 0.36, 0.72, 0.72,
                               _pen("#E6E8EB", 2), QColor("#338CED")).setZValue(3)
            if run_state is not None and "uav_north_m" in run_state:
                estimate = _point(run_state["estimate_north_m"], run_state["estimate_east_m"])
                radius = config.guard_max_distance_m
                frame = frame.united(QRectF(estimate.x() - radius - 2, estimate.y() - radius - 2,
                                            2 * radius + 4, 2 * radius + 4))
                for distance, style in ((config.guard_min_distance_m, Qt.PenStyle.DashLine),
                                        (config.guard_max_distance_m, Qt.PenStyle.DotLine)):
                    pen = _pen("#65717D", 1)
                    pen.setStyle(style)
                    drawing.addEllipse(estimate.x() - distance, estimate.y() - distance,
                                       2 * distance, 2 * distance, pen).setZValue(1)
                drone = _point(run_state["uav_north_m"], run_state["uav_east_m"])
                sight_color = "#0AAC23" if run_state.get("line_of_sight") else "#DC3C3D"
                sight_pen = _pen(sight_color, 1.2)
                sight_pen.setStyle(Qt.PenStyle.DashLine)
                drawing.addLine(drone.x(), drone.y(), estimate.x(), estimate.y(), sight_pen).setZValue(2)
                drawing.addEllipse(drone.x() - 0.48, drone.y() - 0.48, 0.96, 0.96,
                                   _pen("#E6E8EB", 2), QColor("#121519")).setZValue(4)

        # Keep grid line counts bounded for scenes with distant obstacles.
        step = max(2, math.ceil(max(frame.width(), frame.height()) / 36))
        grid_pen = _pen("#1F242B")
        for east in range(math.ceil(frame.left() / step) * step, math.floor(frame.right()) + 1, step):
            drawing.addLine(east, frame.top(), east, frame.bottom(), grid_pen).setZValue(-1)
        for screen_y in range(math.ceil(frame.top() / step) * step, math.floor(frame.bottom()) + 1, step):
            drawing.addLine(frame.left(), screen_y, frame.right(), screen_y, grid_pen).setZValue(-1)
        drawing.setSceneRect(frame)
        self.fitInView(frame, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fitInView(self._drawing.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
