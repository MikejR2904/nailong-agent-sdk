"""Simple AI driver: decides purchases, raises cash, and builds houses.

The engine (`game.Game`) never imports this module; it is passed in as a
pluggable driver, so a human REPL could replace it without touching rules.
"""


class AIPlayer:
    def __init__(self, reserve: int = 100, build_reserve: int = 300):
        self.reserve = reserve
        self.build_reserve = build_reserve

    # -- purchasing ---------------------------------------------------------
    def decide_buy(self, game, player, idx: int) -> bool:
        sp = game.board.spaces[idx]
        price = sp.price
        if price is None or player.cash < price:
            return False

        # Completing a color group is always worth it.
        if sp.group and sp.group not in ("railroad", "utility"):
            members = game.board.spaces_in_group(sp.group)
            owned = sum(1 for i in members if game.props[i].owner == player.index)
            if owned == len(members) - 1:
                return True

        # Railroads/utilities are cheap income; grab them when comfortable.
        if sp.kind in ("railroad", "utility") and player.cash >= price + 50:
            return True

        # Otherwise buy if we keep a cash cushion.
        if player.cash - price >= self.reserve:
            return True

        # Cheap early properties are worth stretching for.
        if price <= 120 and player.cash >= price:
            return True
        return False

    # -- raising cash when a debt is due ------------------------------------
    def manage_assets(self, game, player, needed: int) -> None:
        guard = 0
        while player.cash < needed and guard < 200:
            guard += 1
            if not self._sell_one_house(game, player):
                break
        guard = 0
        while player.cash < needed and guard < 200:
            guard += 1
            if not self._mortgage_one(game, player):
                break

    def _sell_one_house(self, game, player) -> bool:
        best = None
        for i, st in enumerate(game.props):
            if st.owner == player.index and st.houses > 0:
                if best is None or st.houses > game.props[best].houses:
                    best = i
        if best is None:
            return False
        return game.sell_house(player, best)

    def _mortgage_one(self, game, player) -> bool:
        for i, st in enumerate(game.props):
            if st.owner == player.index and not st.mortgaged and st.houses == 0:
                if game.mortgage(player, i):
                    return True
        return False

    # -- building -----------------------------------------------------------
    def manage_build(self, game, player) -> None:
        if player.cash < 500:
            return
        for group in list(game.board.group_members):
            if group in ("railroad", "utility"):
                continue
            members = game.board.spaces_in_group(group)
            if not all(game.props[i].owner == player.index for i in members):
                continue
            if any(game.props[i].mortgaged for i in members):
                continue
            guard = 0
            while guard < 40:
                guard += 1
                minh = min(game.props[i].houses for i in members)
                if minh >= 5:
                    break
                candidates = [i for i in members if game.props[i].houses == minh]
                idx = candidates[0]
                cost = game.board.spaces[idx].house_cost or 0
                if player.cash - cost < self.build_reserve:
                    break
                if not game.build_house(player, idx):
                    break
