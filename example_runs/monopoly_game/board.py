"""Board data model for Monopoly.

Pure data: the 40 spaces plus precomputed group maps. No mutable game state
lives here (ownership/houses/mortgages live in game.PropertyState).
"""

from dataclasses import dataclass


@dataclass
class Space:
    index: int
    kind: str            # go, property, railroad, utility, chance,
                         # community_chest, tax, jail, go_to_jail, free_parking
    name: str
    price: int | None = None
    group: str | None = None
    rents: tuple | None = None      # (base, 1h, 2h, 3h, 4h, hotel)
    house_cost: int | None = None
    tax: int | None = None


# (kind, name, price, group, rents, house_cost, tax)
_RAW = [
    ("go", "GO", None, None, None, None, None),
    ("property", "Mediterranean Avenue", 60, "brown", (2, 10, 30, 90, 160, 250), 50, None),
    ("community_chest", "Community Chest", None, None, None, None, None),
    ("property", "Baltic Avenue", 60, "brown", (4, 20, 60, 180, 320, 450), 50, None),
    ("tax", "Income Tax", None, None, None, None, 200),
    ("railroad", "Reading Railroad", 200, "railroad", None, None, None),
    ("property", "Oriental Avenue", 100, "lightblue", (6, 30, 90, 270, 400, 550), 50, None),
    ("chance", "Chance", None, None, None, None, None),
    ("property", "Vermont Avenue", 100, "lightblue", (6, 30, 90, 270, 400, 550), 50, None),
    ("property", "Connecticut Avenue", 120, "lightblue", (8, 40, 100, 300, 450, 600), 50, None),
    ("jail", "Jail / Just Visiting", None, None, None, None, None),
    ("property", "St. Charles Place", 140, "pink", (10, 50, 150, 450, 625, 750), 100, None),
    ("utility", "Electric Company", 150, "utility", None, None, None),
    ("property", "States Avenue", 140, "pink", (10, 50, 150, 450, 625, 750), 100, None),
    ("property", "Virginia Avenue", 160, "pink", (12, 60, 180, 500, 700, 900), 100, None),
    ("railroad", "Pennsylvania Railroad", 200, "railroad", None, None, None),
    ("property", "St. James Place", 180, "orange", (14, 70, 200, 550, 750, 950), 100, None),
    ("community_chest", "Community Chest", None, None, None, None, None),
    ("property", "Tennessee Avenue", 180, "orange", (14, 70, 200, 550, 750, 950), 100, None),
    ("property", "New York Avenue", 200, "orange", (16, 80, 220, 600, 800, 1000), 100, None),
    ("free_parking", "Free Parking", None, None, None, None, None),
    ("property", "Kentucky Avenue", 220, "red", (18, 90, 250, 700, 875, 1050), 150, None),
    ("chance", "Chance", None, None, None, None, None),
    ("property", "Indiana Avenue", 220, "red", (18, 90, 250, 700, 875, 1050), 150, None),
    ("property", "Illinois Avenue", 240, "red", (20, 100, 300, 750, 925, 1100), 150, None),
    ("railroad", "B&O Railroad", 200, "railroad", None, None, None),
    ("property", "Atlantic Avenue", 260, "yellow", (22, 110, 330, 800, 975, 1150), 150, None),
    ("property", "Ventnor Avenue", 260, "yellow", (22, 110, 330, 800, 975, 1150), 150, None),
    ("utility", "Water Works", 150, "utility", None, None, None),
    ("property", "Marvin Gardens", 280, "yellow", (24, 120, 360, 850, 1025, 1200), 150, None),
    ("go_to_jail", "Go To Jail", None, None, None, None, None),
    ("property", "Pacific Avenue", 300, "green", (26, 130, 390, 900, 1100, 1275), 200, None),
    ("property", "North Carolina Avenue", 300, "green", (26, 130, 390, 900, 1100, 1275), 200, None),
    ("community_chest", "Community Chest", None, None, None, None, None),
    ("property", "Pennsylvania Avenue", 320, "green", (28, 150, 450, 1000, 1200, 1400), 200, None),
    ("railroad", "Short Line", 200, "railroad", None, None, None),
    ("chance", "Chance", None, None, None, None, None),
    ("property", "Park Place", 350, "darkblue", (35, 175, 500, 1100, 1300, 1500), 200, None),
    ("tax", "Luxury Tax", None, None, None, None, 100),
    ("property", "Boardwalk", 400, "darkblue", (50, 200, 600, 1400, 1700, 2000), 200, None),
]

JAIL_INDEX = 10
GO_INDEX = 0


class Board:
    """Immutable board: ordered spaces plus precomputed group maps."""

    def __init__(self):
        self.spaces = [Space(i, *row) for i, row in enumerate(_RAW)]
        self.group_members: dict[str, list[int]] = {}
        for sp in self.spaces:
            if sp.group:
                self.group_members.setdefault(sp.group, []).append(sp.index)

    def spaces_in_group(self, group: str) -> list[int]:
        return list(self.group_members.get(group, []))

    def is_full_group(self, owner, group: str, props) -> bool:
        members = self.spaces_in_group(group)
        return bool(members) and all(props[i].owner == owner for i in members)

    def property_indices(self) -> list[int]:
        return [sp.index for sp in self.spaces if sp.kind == "property"]
