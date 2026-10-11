"""Entry point: `python main.py` (no args, no stdin).

Seeds the RNG (default constant, overridable via MONOPOLY_SEED), creates four
AI players, runs a full deterministic game, and prints the log plus final
standings. Exit code 0 on a completed game.
"""

import os
import sys

from ai import AIPlayer
from game import Game
from render import render_board, render_log, render_standings


def main():
    seed = int(os.environ.get("MONOPOLY_SEED", "42"))
    max_turns = int(os.environ.get("MONOPOLY_MAX_TURNS", "300"))

    game = Game(seed=seed, num_players=4, max_turns=max_turns, ai=AIPlayer())
    print(f"Monopoly auto-play demo (seed={seed}, max_turns={max_turns})")
    print("=" * 60)

    winner = game.run()

    print(render_log(game))
    print(render_board(game))
    print(render_standings(game))

    if winner is None:
        print("ERROR: no winner determined", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
