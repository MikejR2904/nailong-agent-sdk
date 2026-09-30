# SITE_PLAN.md — Initial Site Plan

## 1. Given constraints (not decisions)

- **Lot:** rectangle, 18 m wide (street-facing) × 25 m deep.
- **Coordinate system:** origin (0,0) at the **front-left corner of the lot**.
  - `x` runs along the 18 m street-facing width: 0 → 18.
  - `y` runs with depth into the lot away from the street: 0 → 25 (y = 0 at the street).
- **Required setbacks from the lot boundary:**
  - Front (street side, low y): **3 m** → buildable line at `y = 3`.
  - Left side (low x): **1.5 m** → buildable line at `x = 1.5`.
  - Right side (high x): **1.5 m** → buildable line at `x = 16.5`.
  - Rear (high y): **3 m** → buildable line at `y = 22`.

### Buildable envelope

| Edge | Line | Value |
|------|------|-------|
| Front | y = 3 | 3 m from street |
| Rear | y = 22 | 3 m from rear boundary |
| Left | x = 1.5 | 1.5 m from left boundary |
| Right | x = 16.5 | 1.5 m from right boundary |

Buildable rectangle: **(1.5, 3) → (16.5, 22)** = **15 m wide × 19 m deep = 285 m²**.

## 2. Design decisions

### 2.1 House footprint

I place the house **toward the front** of the buildable envelope so the client
gets a genuinely usable backyard, and I use the **full buildable width** (15 m)
so a single-story 4-bedroom + 2-car-garage plan fits comfortably without
crowding the side setbacks.

**House footprint (bounding rectangle): (1.5, 3) → (16.5, 15)**
= **15 m wide × 12 m deep = 180 m²** (single story).

Internal zoning (for reference; not a structural drawing):

| Zone | Rectangle | Size | Area | Notes |
|------|-----------|------|------|-------|
| 2-car garage | (1.5, 3) → (7.5, 9) | 6 m × 6 m | 36 m² | Garage door faces the street (front edge `y = 3`). |
| Entry / living / kitchen | (7.5, 3) → (16.5, 9) | 9 m × 6 m | 54 m² | Front-facing, opens to backyard. |
| Bedroom wing (4 bedrooms + baths) | (1.5, 9) → (16.5, 15) | 15 m × 6 m | 90 m² | Rear of house, quiet side. |

- **Garage** is on the **left (low-x)** side, so the driveway runs straight in
  from the street along the left setback.
- **Backyard** is the remaining buildable depth behind the house.

### 2.2 Backyard

**Backyard: (1.5, 15) → (16.5, 22)** = **15 m wide × 7 m deep = 105 m²** of
usable, fenced, private outdoor space — comfortably satisfies "some usable
backyard space."

### 2.3 Driveway

**Driveway: x = 1.5 → 7.5, y = 0 → 3** (6 m wide), running from the street to
the garage door at `y = 3`.

## 3. Fence line

The fence follows the property boundary **inside the setbacks**, enclosing the
side and rear yard and the frontage except for the driveway opening. It runs on
the setback lines themselves (x = 1.5, x = 16.5, y = 22, and the front at y = 3).

**Fence path (open polyline, left side → rear → right side → front):**

1. `(1.5, 3)` → `(1.5, 22)` — left side fence, 19 m (along left setback).
2. `(1.5, 22)` → `(16.5, 22)` — rear fence, 15 m (along rear setback).
3. `(16.5, 22)` → `(16.5, 3)` — right side fence, 19 m (along right setback).
4. `(16.5, 3)` → `(7.5, 3)` — front fence, 9 m (along front setback, right of driveway).

**Driveway opening (no fence):** `x = 1.5 → 7.5` at `y = 3` (6 m gate/opening
for vehicle access to the garage).

**Total fence length:** 19 + 15 + 19 + 9 = **62 m**.

## 4. Coordinate summary

| Element | Coordinates | Dimensions |
|---------|-------------|------------|
| Lot | (0, 0) → (18, 25) | 18 m × 25 m |
| Buildable envelope | (1.5, 3) → (16.5, 22) | 15 m × 19 m |
| **House footprint** | **(1.5, 3) → (16.5, 15)** | **15 m × 12 m (180 m²)** |
| Garage | (1.5, 3) → (7.5, 9) | 6 m × 6 m |
| Living / entry | (7.5, 3) → (16.5, 9) | 9 m × 6 m |
| Bedroom wing | (1.5, 9) → (16.5, 15) | 15 m × 6 m |
| Backyard | (1.5, 15) → (16.5, 22) | 15 m × 7 m (105 m²) |
| Driveway | x 1.5–7.5, y 0–3 | 6 m × 3 m |
| Fence | (1.5,3)→(1.5,22)→(16.5,22)→(16.5,3)→(7.5,3) | 62 m total |

## 5. Reasoning notes

- **Front placement + full width:** maximizes backyard depth while keeping the
  house within all four setbacks; a 15 m × 12 m single-story footprint easily
  accommodates 4 bedrooms, living areas, and a 2-car garage.
- **Garage on the left:** keeps the driveway on one side, leaving the right
  frontage for the entry and landscaping.
- **Fence on setback lines:** the fence sits exactly on the buildable setback
  lines, so it never encroaches on the required setbacks and cleanly encloses
  the private yard; the front is left open only at the driveway for vehicle
  access.
