"""Urban route geometry shared by the preview and offline simulator."""

import math


# Coordinates relative to the target start, in north/east meters.
URBAN_ROUTE = ((0, 0), (16, 0), (16, 12), (0, 12))
_FORWARD = tuple(zip(URBAN_ROUTE, (*URBAN_ROUTE[1:], URBAN_ROUTE[0])))
_REVERSE = tuple((end, start) for start, end in reversed(_FORWARD))
_LENGTH = sum(math.dist(start, end) for start, end in _FORWARD)


def target_position(distance_m: float, direction: str) -> tuple[float, float]:
    """Return north and east for a traveled distance on the closed route."""
    distance = distance_m % _LENGTH
    for start, end in _REVERSE if direction == "reverse" else _FORWARD:
        length = math.dist(start, end)
        if distance < length:
            ratio = distance / length
            return (start[0] + ratio * (end[0] - start[0]),
                    start[1] + ratio * (end[1] - start[1]))
        distance -= length
    return URBAN_ROUTE[0]
