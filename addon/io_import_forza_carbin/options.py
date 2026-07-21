"""Single source of truth for import options.

Previously the option set was duplicated four ways (module-scope enum lists, the browse
operator's properties, the AddonPreferences properties, and core.py's `globals().get(...)`
defaults). `ImportOptions` is the one definition; the operators/preferences build it and the
importer consumes it. The enum item lists below feed the Blender EnumProperties.

Note: the legacy `shader_processor` switch is intentionally absent - the rewrite has a single
data-driven material pipeline with no FH3-handler/generic fork.
"""

from dataclasses import dataclass, field, asdict

# --- Blender EnumProperty item lists (label/desc preserved from the original addon) ---
LOD_ITEMS = [
    ("1", "LODS (highest)", "Source/highest-detail meshes"),
    ("2", "LOD0", ""),
    ("4", "LOD1", ""),
    ("8", "LOD2", ""),
    ("16", "LOD3", ""),
]
DRAW_ITEMS = [
    ("1", "Exterior", ""),
    ("2", "Cockpit", ""),
    ("4", "Shadow", ""),
    ("8", "Hood", ""),
    ("16", "Windshield Reflection", ""),
]
SUSP_ITEMS = [
    ("0", "Skeleton", "Position from the car skeleton (needs the skeleton modelbin)"),
    ("1", "Carbin", "Position from the carbin (no GameDB required) — use when no decrypted GameDB"),
    ("2", "GameDB", "Most accurate; requires a decrypted GameDB .slt for this car"),
]


@dataclass
class ImportOptions:
    """All inputs to a single car import. Operators/preferences populate it; the importer reads it."""

    # --- paths / identity ---
    game_path: str = ""
    db_path: str = ""
    media_name: str = ""
    car_body_id: object = None
    car_root_dir: str = ""               # on-disk car folder (Animations\, scene\*_skeleton.gr2)
    # Optional remap roots for raw (non-rip) extraction layouts:
    cars_dir_override: str = None        # remaps internal GAME:\Media\Cars
    tires_dir_override: str = None       # remaps ..\_library\scene\tires
    materials_dir_override: str = None   # remaps ..\_library\materials
    car_zip_path: str = None             # loose or Media car .zip to register in ZipAssetStore

    # --- filtering / build flags ---
    requested_level_of_detail: int = 1 << 0
    requested_draw_group: int = 1 << 0
    hide_decal_transparent_pass: bool = False
    suspension_only: bool = False
    suspension_transform_type: int = 1   # 0=skeleton, 1=carbin, 2=gamedb
    create_spheres: bool = False
    use_materials: bool = True
    create_placeholder_materials: bool = False
    quadrangulate_mesh: bool = False
    use_db: bool = False
    series: int = 0

    # --- fallback tire/body measurements (GameDB supplies these when use_db) ---
    TireModelName: str = ""
    FrontTireWidthMM: float = 245
    OriginalFrontTireAspect: float = 40
    OriginalFrontWheelDiameterIN: float = 19
    FrontWheelDiameterIN: float = 19
    RearTireWidthMM: float = 245
    OriginalRearTireAspect: float = 40
    OriginalRearWheelDiameterIN: float = 19
    RearWheelDiameterIN: float = 19
    ModelWheelbase: float = 2.6
    ModelFrontTrackOuter: float = 1.6
    ModelRearTrackOuter: float = 1.6
    ModelFrontStockRideHeight: float = 0.1
    ModelRearStockRideHeight: float = 0.1
    BottomCenterWheelbasePosX: float = 0
    BottomCenterWheelbasePosY: float = 0
    BottomCenterWheelbasePosZ: float = 0

    def as_dict(self):
        return asdict(self)
