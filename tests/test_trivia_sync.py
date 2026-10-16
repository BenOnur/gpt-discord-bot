import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.trivia.game import TriviaButton, TriviaView


class TriviaSyncTests(unittest.IsolatedAsyncioTestCase):
    def make_view(self):
        view = TriviaView({
            "correct_author_name": "Correct", "content": "A server quote",
            "date_str": "17 October", "url": "https://discord.com/channels/1/2/3",
            "options": ["Wrong A", "Wrong B", "Correct", "Wrong C"],
        })
        self.addCleanup(view.stop)
        view.message = SimpleNamespace(edit=AsyncMock())
        return view

    def interaction(self, user_id):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, display_name=f"Player {user_id}", mention=f"<@{user_id}>"),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_slow_wrong_update_cannot_overwrite_winner(self):
        view = self.make_view()
        wrong_a, wrong_b, correct = view.children[:3]
        entered, release = asyncio.Event(), asyncio.Event()
        published = []

        async def edit(**kwargs):
            if not entered.is_set():
                entered.set()
                await release.wait()
            published.append(kwargs["embed"].to_dict())

        view.message.edit.side_effect = edit
        first, second, winner = [self.interaction(i) for i in (1, 1, 2)]
        with patch("bot.trivia.game.record_trivia_win", return_value=10) as award:
            tasks = [asyncio.create_task(view.handle_guess(first, wrong_a))]
            await entered.wait()
            tasks.append(asyncio.create_task(view.handle_guess(second, wrong_b)))
            tasks.append(asyncio.create_task(view.handle_guess(winner, correct)))
            await asyncio.sleep(0)
            winner.response.defer.assert_awaited_once()
            release.set()
            await asyncio.gather(*tasks)
            award.assert_called_once_with(2, "Player 2", view.data_dir)
        self.assertIn("TEBR", published[-1]["footer"]["text"])
        self.assertTrue(published[-1].get("fields"))
        self.assertTrue(view.answered)
        self.assertTrue(all(button.disabled for button in view.children if isinstance(button, TriviaButton)))

    async def test_concurrent_correct_clicks_award_only_one_winner(self):
        view = self.make_view()
        correct = view.children[2]
        first, second = self.interaction(1), self.interaction(2)
        with patch("bot.trivia.game.record_trivia_win", return_value=10) as award:
            await asyncio.gather(view.handle_guess(first, correct), view.handle_guess(second, correct))
            award.assert_called_once()
        view.message.edit.assert_awaited_once()
        second.followup.send.assert_awaited_once()

    async def test_timeout_cannot_replace_a_winning_result(self):
        view = self.make_view()
        with patch("bot.trivia.game.record_trivia_win", return_value=10):
            await view.handle_guess(self.interaction(1), view.children[2])
        await view.on_timeout()
        final_edit = view.message.edit.await_args.kwargs
        self.assertNotIn("embed", final_edit)
        self.assertTrue(all(button.disabled for button in view.children))
