"""Unit tests for the Monopoly engine invariants (run with unittest/pytest)."""

import unittest

from ai import AIPlayer
from board import Board
from game import Game


class TestBoard(unittest.TestCase):
    def test_space_count(self):
        b = Board()
        self.assertEqual(len(b.spaces), 40)
        self.assertEqual(len(b.property_indices()), 22)

    def test_groups(self):
        b = Board()
        self.assertEqual(len(b.spaces_in_group("railroad")), 4)
        self.assertEqual(len(b.spaces_in_group("utility")), 2)
        self.assertEqual(len(b.spaces_in_group("brown")), 2)


class TestEngine(unittest.TestCase):
    def make(self, seed=1):
        return Game(seed=seed, num_players=4, max_turns=300, ai=AIPlayer())

    def test_passing_go_credits_200(self):
        g = self.make()
        p = g.players[0]
        p.position = 38
        g.advance(p, 5)
        self.assertEqual(p.position, 3)
        self.assertEqual(p.cash, 1700)

    def test_full_group_doubles_rent(self):
        g = self.make()
        p = g.players[0]
        g.props[1].owner = 0
        g.props[3].owner = 0
        self.assertEqual(g.rent_for(1, 7), 4)   # base 2 doubled
        g.props[3].mortgaged = True
        self.assertEqual(g.rent_for(1, 7), 2)   # mortgage breaks the group

    def test_railroad_rent_scales(self):
        g = self.make()
        for i in g.board.spaces_in_group("railroad"):
            g.props[i].owner = 0
        self.assertEqual(g.rent_for(5, 7), 200)

    def test_even_build_enforced(self):
        g = self.make()
        p = g.players[0]
        p.cash = 5000
        for i in g.board.spaces_in_group("brown"):
            g.props[i].owner = 0
        self.assertTrue(g.build_house(p, 1))
        self.assertFalse(g.build_house(p, 1))   # would break even-build
        self.assertTrue(g.build_house(p, 3))

    def test_three_doubles_sends_to_jail(self):
        g = self.make()
        p = g.players[0]
        g.roll_dice = lambda: (3, 3)
        g.take_turn(p)
        self.assertTrue(p.in_jail)

    def test_bankruptcy_transfers_assets(self):
        g = self.make()
        payer, creditor = g.players[0], g.players[1]
        g.props[1].owner = 0
        payer.cash = 10
        g.pay(payer, 500, creditor)
        self.assertTrue(payer.bankrupt)
        self.assertEqual(g.props[1].owner, 1)

    def test_game_terminates_with_one_winner(self):
        g = self.make(seed=42)
        winner = g.run()
        self.assertIsNotNone(winner)
        self.assertIn(winner, g.players)
        self.assertLessEqual(g.turn, 300)

    def test_determinism(self):
        g1 = self.make(seed=7)
        g2 = self.make(seed=7)
        g1.run()
        g2.run()
        self.assertEqual(g1.log_lines, g2.log_lines)


if __name__ == "__main__":
    unittest.main()
