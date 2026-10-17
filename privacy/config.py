from dataclasses import dataclass, field


@dataclass
class Config:
    veh_conf: float = 0.35
    plate_conf: float = 0.10
    face_conf: float = 0.50
    rider_face_conf: float = 0.88
    plate_window_min: int = 400
    plate_window_max: int = 760
    min_plate_w: int = 30
    scan_top: float = 0.30
    scan_conf: float = 0.40
    scan_free_conf: float = 0.85
    face_scan: bool = True
    face_scan_conf: float = 0.70
    min_face_px: int = 24
    head_fallback: bool = False
    scan_levels: tuple = ((0.667, 0.30, 1.0),)
    min_plate_h: int = 10
    max_vehicles: int = 10
    max_persons: int = 14
    obj_tile_top: float = 0.35
    obj_tiles: bool = True
    tile: int = 640
    stride: int = 480
    min_head_px: int = 14
    own_zones: list = field(default_factory=list)
    min_hits: int = 2
    sure_plate: float = 0.35
    sure_face: float = 0.70
    hold_frames: int = 12
    back_extrap: bool = False
    back_frames: int = 20
    margin_plate: float = 0.14
    margin_face: float = 0.18
    margin_px: int = 5
