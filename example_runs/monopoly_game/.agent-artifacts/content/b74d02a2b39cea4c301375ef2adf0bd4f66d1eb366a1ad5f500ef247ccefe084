"""Player state for Monopoly."""

from dataclasses import dataclass


@dataclass
class Player:
    index: int
    name: str
    cash: int = 1500
    position: int = 0
    in_jail: bool = False
    jail_turns: int = 0
    get_out_of_jail_cards: int = 0
    bankrupt: bool = False

    def net_worth(self, game) -> int:
        """Cash plus property value (half price if mortgaged) plus houses."""
        total = self.cash
        for i, st in enumerate(game.props):
            if st.owner != self.index:
                continue
            sp = game.board.spaces[i]
            if sp.price is None:
                continue
            total += sp.price // 2 if st.mortgaged else sp.price
            if st.houses:
                total += st.houses * (sp.house_cost or 0)
        return total

    def owned_indices(self, game) -> list[int]:
        return [i for i, st in enumerate(game.props) if st.owner == self.index]
