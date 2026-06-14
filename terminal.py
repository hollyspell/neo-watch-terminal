"""
Retro terminal enclosure for a 7.5" e-ink display (Waveshare-style).
Coordinate system while building: x = depth (front->back), y = height, z = width.
Rotated at the end so Z is up for printing.
Units: mm
"""
import math
import numpy as np
from manifold3d import Manifold, CrossSection, JoinType
import trimesh

# ---------------- Parameters ----------------
W = 186.0          # overall width
D = 110.0          # overall depth
H = 215.0          # overall height
T = 4.0            # wall thickness

# E-ink panel (from outline drawing)
PANEL_W, PANEL_H = 170.20, 111.20      # outer glass
VIEW_W,  VIEW_H  = 163.20, 97.92       # active area
POCKET_W = PANEL_W + 1.3               # clearance pocket behind bezel
POCKET_H = PANEL_H + 1.3
OPEN_W   = VIEW_W - 2.0                # window slightly smaller than active area
OPEN_H   = VIEW_H - 2.0
BEZEL_T  = 2.0                         # lip the panel sits against

# Cord exit
CORD_D = 12.0

# Side profile (x = depth, y = height), CCW
FACE_X   = 55.0    # x position of the vertical screen face
FACE_LO  = 70.0    # bottom of screen face
FACE_HI  = 205.0   # top of screen face
profile = [
    (0.0,    0.0),
    (D,      0.0),
    (D,      H),
    (75.0,   H),          # flat top
    (FACE_X, FACE_HI),    # chamfer down to screen face
    (FACE_X, FACE_LO),    # vertical screen face
    (0.0,    25.0),       # slanted button panel down to front lip
]

# ---------------- Slide-in panel parameters ----------------
RAIL_W = 4.0          # rail width (inward from wall)
RAIL_H = 3.0          # rail height
RAIL_INSET = 8.0      # inset from inner wall to rail
PLATE_T = 2.5         # plate thickness
PLATE_CLEARANCE = 0.4 # side clearance for plate
DETENT_H = 0.8        # locking bump height
DETENT_W = 6.0        # locking bump width along rail
DETENT_POS = 20.0     # distance from back wall to detent center

# Derived
INNER_W = W - 2 * T                    # inner cavity width
PLATE_W = INNER_W - 2 * PLATE_CLEARANCE  # plate width
PLATE_D = D - 2 * T - 2.0              # plate depth (slight clearance front/back)

# ---------------- Outer shell ----------------
outer = CrossSection([profile]).extrude(W)
inner = CrossSection([profile]).offset(-T, JoinType.Miter).extrude(W - 2 * T).translate([0, 0, T])
body = outer - inner

# Open entire bottom for slide-in panel
bottom_open = Manifold.cube([D - 16, 10, W - 16]).translate([8, -2, 8])
body -= bottom_open

# ---------------- Rails for slide-in panel ----------------
# Two rails running front-to-back on the inner bottom, inset from side walls
# Left rail (low z side)
rail_z_left = T + RAIL_INSET
rail_left = Manifold.cube([PLATE_D + 4, RAIL_H, RAIL_W]).translate([T - 1, T, rail_z_left])
body += rail_left

# Right rail (high z side)
rail_z_right = W - T - RAIL_INSET - RAIL_W
rail_right = Manifold.cube([PLATE_D + 4, RAIL_H, RAIL_W]).translate([T - 1, T, rail_z_right])
body += rail_right

# Locking detent bumps on top of each rail
for rz in [rail_z_left, rail_z_right]:
    bump = Manifold.cube([DETENT_W, DETENT_H, RAIL_W]).translate([
        D - T - DETENT_POS - DETENT_W / 2,  # near back
        T + RAIL_H,                            # on top of rail
        rz
    ])
    body += bump

# Front lip / stop — small blocks at the front of each rail to guide the plate
for rz in [rail_z_left, rail_z_right]:
    guide = Manifold.cube([3, RAIL_H + PLATE_T + 1, RAIL_W]).translate([
        T - 1, T, rz
    ])
    # Don't add these — they'd block the plate from sliding in
    # Instead the front wall lip serves as the stop

# ---------------- Screen window + pocket ----------------
cy = (FACE_LO + FACE_HI) / 2.0          # screen center height
cz = W / 2.0

window = Manifold.cube([T + 4, OPEN_H, OPEN_W], True).translate([FACE_X + T / 2, cy, cz])
pocket = Manifold.cube([T, POCKET_H, POCKET_W], True).translate(
    [FACE_X + BEZEL_T + T / 2, cy, cz])     # starts 2 mm behind the front surface
body = body - window - pocket

# ---------------- Button hole (slanted panel) ----------------
# 16mm round mounting hole for the square-cap momentary button
panel_ang = math.degrees(math.atan2(70 - 25, FACE_X))
BTN_R = 16.0 / 2 + 0.2
btn_cut = Manifold.cylinder(30, BTN_R, circular_segments=64)
btn_cut = btn_cut.rotate([0, 0, panel_ang + 90])
btn_cut = btn_cut.translate([27.5, 47.5, cz - 55.0])
body -= btn_cut

# ---------------- Cord hole (back, near bottom) ----------------
cord = Manifold.cylinder(T + 6, CORD_D / 2, circular_segments=64)
cord = cord.rotate([0, 90, 0]).translate([D - T - 3, 22.0, cz])
body -= cord

# ---------------- Vent slots (back wall) ----------------
for off in (36.0, 56.0, 76.0):
    slot = Manifold.cube([T + 6, 50, 6], True).translate([D - T / 2, 55.0, cz + off])
    body -= slot

# ================ SLIDE-IN PLATE (separate piece) ================
plate = Manifold.cube([PLATE_D, PLATE_T, PLATE_W]).translate([
    T + 1,                      # x: starts just inside front wall
    T + RAIL_H,                 # y: sits on top of rails
    T + PLATE_CLEARANCE         # z: centered with clearance
])

# Vent slots in the back 2/3 of the plate
vent_region_start = D / 3           # front 1/3 is solid (finger grip area)
vent_slot_w = 4.0
vent_gap = 8.0
n_vent_rows = 5
n_vent_cols = int((PLATE_W - 40) / (vent_slot_w + vent_gap))
vent_start_z = T + PLATE_CLEARANCE + 20  # inset from plate edges

for row in range(n_vent_rows):
    vx = vent_region_start + 10 + row * 14
    for col in range(n_vent_cols):
        vz = vent_start_z + col * (vent_slot_w + vent_gap)
        vent = Manifold.cube([10, PLATE_T + 2, vent_slot_w]).translate([
            vx, T + RAIL_H - 1, vz
        ])
        plate -= vent

# Detent notches on underside of plate (matching the rail bumps)
for rz_offset in [RAIL_INSET, INNER_W - RAIL_INSET - RAIL_W]:
    notch = Manifold.cube([DETENT_W + 0.6, DETENT_H + 0.4, RAIL_W + 0.6]).translate([
        D - T - DETENT_POS - DETENT_W / 2 - 0.3,
        T + RAIL_H - DETENT_H - 0.2,
        T + rz_offset - 0.3
    ])
    plate -= notch

# Finger notch at front edge of plate for easy removal
finger = Manifold.cylinder(20, 8, circular_segments=32)
finger = finger.rotate([90, 0, 0])
finger = finger.translate([T + 1, T + RAIL_H + PLATE_T / 2, W / 2])
plate -= finger

# ================ Orient and export ================
# Rotate both so Z is up for printing
body = body.rotate([90, 0, 0]).translate([0, W, 0])
plate = plate.rotate([90, 0, 0]).translate([0, W, 0])

# Export main body
mesh = body.to_mesh()
tm = trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3],
                     faces=np.asarray(mesh.tri_verts))
print("Body — watertight:", tm.is_watertight, "| volume cm^3:", round(tm.volume / 1000, 1))
print("  bounds (mm):", np.round(tm.bounds, 1).tolist())
tm.export("/home/claude/eink_terminal.stl")

# Export plate
mesh_p = plate.to_mesh()
tm_p = trimesh.Trimesh(vertices=np.asarray(mesh_p.vert_properties)[:, :3],
                       faces=np.asarray(mesh_p.tri_verts))
print("Plate — watertight:", tm_p.is_watertight, "| volume cm^3:", round(tm_p.volume / 1000, 1))
print("  bounds (mm):", np.round(tm_p.bounds, 1).tolist())
tm_p.export("/home/claude/eink_plate.stl")

print("exported both")
