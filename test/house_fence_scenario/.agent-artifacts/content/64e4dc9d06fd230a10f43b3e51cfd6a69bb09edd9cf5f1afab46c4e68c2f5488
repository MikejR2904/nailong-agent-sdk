# FENCE_LAYOUT.md — Property Fence Design

Authoritative fence design for the lot. Derived from **SITE_PLAN.md** (original
site plan) and **HOUSE_LAYOUT.md** (the house agent's actual final footprint).
Where the two disagree about the house, this design follows HOUSE_LAYOUT.md.

---

## 1. Did SITE_PLAN.md and HOUSE_LAYOUT.md agree?

**No — they differ, in the house footprint depth only.**

| Source | House footprint | Size | Area |
|--------|-----------------|------|------|
| SITE_PLAN.md (original plan) | (1.5, 3) → (16.5, 15) | 15 m × 12 m | 180 m² |
| HOUSE_LAYOUT.md (actually built) | (1.5, 3) → (16.5, 16) | 15 m × 13 m | 195 m² |

- **Same width** (full 15 m buildable width) and **same front position**
  (front wall on the `y = 3` front setback line).
- **The actual house is 1 m deeper**: rear wall at `y = 16` instead of `y = 15`.
- Everything else — lot (18 m × 25 m), setbacks (front 3 m, left 1.5 m,
  right 1.5 m, rear 3 m), buildable envelope (1.5, 3) → (16.5, 22), and the
  driveway (x = 1.5 → 7.5, y = 0 → 3) — is identical in both files.

So the house was **not** built exactly as originally planned; it was revised
1 m deeper. This design is built around the **actual** footprint
`(1.5, 3) → (16.5, 16)`.

---

## 2. What I changed in my own design because of the difference

The fence runs on the **setback lines**, which are fixed by the lot and the
setbacks and are the same in both files, so the fence *path* is unchanged.
But the actual (deeper) house changes three things I had to design around:

1. **Rear yard depth.** The fenced rear yard is now `y = 16 → 22` = **6 m deep
   (90 m²)**, not the 7 m (105 m²) the original plan implied. The fence still
   fully encloses it; the rear fence stays at `y = 22`.
2. **Free-standing side-fence length.** Each side fence is attached to the house
   wall from `y = 3` to `y = 16` (**13 m**) and free-standing from `y = 16` to
   `y = 22` (**6 m**). Under the original plan it would have been attached
   3 → 15 (12 m) and free 15 → 22 (7 m). I sized and placed the gates against
   the **actual** free-standing length.
3. **Gate position.** I placed the pedestrian gate in the free-standing portion
   of the side fence (`y = 17.0 → 18.0`), which is clear of the house only
   because the actual rear wall is at `y = 16`. Using the original `y = 15`
   would have shifted the gate.

I also **removed the original plan's front fence segment**
`(16.5, 3) → (7.5, 3)`. The actual house front wall occupies `y = 3` across the
full 15 m buildable width, so a fence on that line would be coincident with the
house facade. The house itself closes the front of the fenced yard, so no front
fence is built.

---

## 3. Fence design

**Fence line:** on the setback lines — `x = 1.5` (left), `x = 16.5` (right),
`y = 22` (rear) — i.e. 1.5 m inside the side lot lines and 3 m inside the rear
lot line. The front is closed by the house facade.

**Fence path (open polyline, left side → rear → right side):**

1. `(1.5, 3)` → `(1.5, 22)` — **left side fence, 19 m** (attached to the house
   left wall from `y = 3` to `y = 16`; free-standing from `y = 16` to `y = 22`).
2. `(1.5, 22)` → `(16.5, 22)` — **rear fence, 15 m**.
3. `(16.5, 22)` → `(16.5, 3)` — **right side fence, 19 m** (attached to the
   house right wall from `y = 3` to `y = 16`; free-standing from `y = 16` to
   `y = 22`).

**Front closure:** the house facade (front wall at `y = 3`, `x = 1.5 → 16.5`) —
no fence segment.

**Total fence length: 19 + 15 + 19 = 53 m.**

**Gates:**

- **Pedestrian gate, 1.0 m wide**, in the **left side fence** at
  `y = 17.0 → 18.0` (`x = 1.5`). Gives access from the front/side yard into the
  rear yard. It sits in the free-standing portion of the fence, clear of the
  house (rear wall `y = 16`).
- **Pedestrian gate, 1.0 m wide**, in the **right side fence** at
  `y = 17.0 → 18.0` (`x = 16.5`), matching the left gate (optional second
  access; omit if a single gate is preferred).
- **No driveway gate.** The driveway (`x = 1.5 → 7.5`, `y = 0 → 3`) and the
  garage door are in the front yard, outside the fenced rear yard, so vehicles
  never cross the fence line.

---

## 4. Consistency check against the actual house

- **Actual house footprint:** `(1.5, 3) → (16.5, 16)`.
- The fence at `x = 1.5` and `x = 16.5` is flush with the house side walls
  (the house uses the full buildable width), attached from `y = 3` to `y = 16`.
- The rear fence at `y = 22` is **6 m behind the actual rear wall** (`y = 16`)
  and does not touch the house.
- **No fence line crosses the house.** The enclosed private yard is the rear
  yard `(1.5, 16) → (16.5, 22)` = 15 m × 6 m = **90 m²**.
- Every fence line is **inside the legal lot line**: 1.5 m from the side lot
  lines (`x = 0`, `x = 18`) and 3 m from the rear lot line (`y = 25`).

---

## 5. Coordinate summary

| Element | Coordinates | Dimensions |
|---------|-------------|------------|
| Lot | (0, 0) → (18, 25) | 18 m × 25 m |
| Buildable envelope | (1.5, 3) → (16.5, 22) | 15 m × 19 m (285 m²) |
| **House footprint (actual, per HOUSE_LAYOUT.md)** | **(1.5, 3) → (16.5, 16)** | **15 m × 13 m (195 m²)** |
| Driveway | x 1.5 → 7.5, y 0 → 3 | 6 m × 3 m |
| Enclosed rear yard | (1.5, 16) → (16.5, 22) | 15 m × 6 m (90 m²) |
| Left side fence | (1.5, 3) → (1.5, 22) | 19 m |
| Rear fence | (1.5, 22) → (16.5, 22) | 15 m |
| Right side fence | (16.5, 22) → (16.5, 3) | 19 m |
| Front closure | house facade, y = 3, x 1.5 → 16.5 | (no fence) |
| **Fence total** | (1.5,3)→(1.5,22)→(16.5,22)→(16.5,3) | **53 m** |
| Pedestrian gate (left) | x = 1.5, y 17.0 → 18.0 | 1.0 m |
| Pedestrian gate (right) | x = 16.5, y 17.0 → 18.0 | 1.0 m |
