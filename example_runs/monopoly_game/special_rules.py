"""The "Let's Get Rich" special rule.

A single, self-contained house rule layered on top of the core engine:

* A shared **Get Rich Pot** starts empty.
* The bank matches a **10% levy** on every payment a player makes (rent, tax,
  fees, the jail fine, and card payments) and drops that match into the pot.
  The levy is funded by the bank, so it never changes the amount the payer
  pays or the creditor receives -- every existing money flow is untouched.
* Landing on **Free Parking** collects the entire pot.

Why this shape: the core engine keeps its "all rules live in game.py" design,
and this module only exposes small pure helpers that `game.py` calls at two
well-defined seams (a successful payment, and the Free Parking landing). All
state lives on the `Game` instance (`game.get_rich_pot`), so the rule is
deterministic and unit-testable exactly like the rest of the engine.
"""

LEVY_RATE = 10   # the bank matches 1/10 of every payment
MIN_LEVY = 1     # a non-zero payment always seeds at least $1
POT_START = 0


def init_pot(game):
    """Attach the Get Rich Pot to a freshly constructed game."""
    game.get_rich_pot = POT_START


def levy(amount):
    """The bank's Get Rich match on a payment of `amount` (0 if no payment)."""
    if amount <= 0:
        return 0
    return max(MIN_LEVY, amount // LEVY_RATE)


def credit_pot(game, amount):
    """Bank-match a successful payment into the pot; return the amount added."""
    cut = levy(amount)
    if cut:
        game.get_rich_pot += cut
    return cut


def collect_pot(game, player):
    """Free Parking payout: hand the whole pot to `player`; return the amount."""
    amount = game.get_rich_pot
    if amount:
        player.cash += amount
        game.get_rich_pot = 0
    return amount
