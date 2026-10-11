"""Rendering helpers: turn the engine's state into human-readable text.

Kept separate from `game.py` so the view can be swapped (CLI, GUI, JSON)
without touching rules.
"""


def render_standings(game):
    lines = ["", "=" * 60, "FINAL STANDINGS", "=" * 60]
    ranked = sorted(game.players, key=lambda p: p.net_worth(game), reverse=True)
    for p in ranked:
        status = "BANKRUPT" if p.bankrupt else f"${p.net_worth(game)}"
        props = len(p.owned_indices(game))
        lines.append(
            f"  {p.name:<10} cash ${p.cash:<6} properties {props:<3} "
            f"net worth {status}"
        )
    lines.append("=" * 60)
    if game.winner is not None:
        lines.append(f"WINNER: {game.winner.name} "
                     f"(net worth ${game.winner.net_worth(game)})")
    lines.append("=" * 60)
    return "\n".join(lines)


def render_board(game):
    lines = ["", "BOARD OWNERSHIP", "-" * 60]
    for sp in game.board.spaces:
        st = game.props[sp.index]
        if st.owner is None:
            owner = "-"
        else:
            owner = game.players[st.owner].name
        extra = ""
        if st.houses == 5:
            extra = " [HOTEL]"
        elif st.houses:
            extra = f" [{st.houses} houses]"
        if st.mortgaged:
            extra += " [mortgaged]"
        lines.append(f"  {sp.index:>2} {sp.name:<26} {owner}{extra}")
    return "\n".join(lines)


def render_log(game):
    return "\n".join(game.log_lines)
