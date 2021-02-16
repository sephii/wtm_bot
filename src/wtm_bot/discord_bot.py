import asyncio
import dataclasses
import difflib
import enum
import io
import logging
import os
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import List

import discord
from wtm_bot.wtm import Difficulty, WtmSession

NB_SHOTS = 12
GUESS_TIME_SECONDS = 30
MAX_COMBO = 2

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class GameStatus(enum.Enum):
    IDLE = enum.auto()
    WAITING_FOR_GUESSES = enum.auto()
    LOADING = enum.auto()


class CommandType(enum.Enum):
    START = "start"
    HELP = "help"


@dataclass(frozen=True)
class Command:
    type: CommandType
    args: List[str]


@dataclass(frozen=True)
class Combo:
    player: str
    combo: int


@dataclass(frozen=True)
class FuzzyResult:
    match: str
    score: float


def _fuzzy_compare_str(str1, str2):
    str1_lower = str1.lower()
    str1_parts = str1_lower.split(":") + [str1_lower]
    max_ratio = max(
        difflib.SequenceMatcher(lambda x: x in " \t", str1_part, str2).ratio()
        for str1_part in str1_parts
    )

    return max_ratio


def fuzzy_compare(solutions, guess):
    """
    Compare a list of solutions and a guess, and return the highest scoring one
    as a `FuzzyResult`, or `None` if there’s no match.
    """
    guess = guess.lower()
    results = [
        FuzzyResult(match=solution, score=_fuzzy_compare_str(solution, guess))
        for solution in solutions
    ]

    return max(results, key=lambda item: item.score, default=None)


class Game:
    def __init__(self, *, wtm_user, wtm_password, tmdb_token):
        self.wtm_user = wtm_user
        self.wtm_password = wtm_password

        self.scores = defaultdict(int)
        self.wtm_session = WtmSession(tmdb_token)
        self.status = GameStatus.IDLE
        self.guess_timer = None
        self.shots_queue = asyncio.Queue()
        self.current_shot = None
        self.current_combo = None
        self.skip_votes = set()
        self.signal_subscribers = defaultdict(list)

    @property
    def nb_players(self):
        return len(self.scores)

    async def handle_guess(self, player, guess, **kwargs):
        if player not in self.scores:
            self.scores[player] = 0

        fuzzy_result = fuzzy_compare(
            set([self.current_shot.movie_title])
            | self.current_shot.movie_alternative_titles,
            guess,
        )

        if fuzzy_result and fuzzy_result.score >= 0.8:
            if self.current_combo and self.current_combo.player == player:
                self.current_combo = dataclasses.replace(
                    self.current_combo,
                    combo=min(self.current_combo.combo + 1, MAX_COMBO),
                )
            else:
                self.current_combo = Combo(player=player, combo=1)
            self.scores[player] += self.current_combo.combo
            self.status = GameStatus.LOADING
            await self.emit_signal(
                "correct_guess", player=player, movie_title=fuzzy_result.match, **kwargs
            )
            self.guess_timer.cancel()
        else:
            if self.current_combo and self.current_combo.player == player:
                self.current_combo = None
            await self.emit_signal(
                "incorrect_guess", player=player, guess=guess, **kwargs
            )

    async def game_loop(self, difficulty):
        self.status = GameStatus.LOADING
        await self.wtm_session.login(self.wtm_user, self.wtm_password)
        await self.wtm_session.set_difficulty(difficulty)
        self.populate_queue_task = asyncio.create_task(self.populate_queue())
        guess_loop_task = asyncio.create_task(self.guess_loop())

        await self.populate_queue_task
        await guess_loop_task

    async def populate_queue(self):
        for i in range(NB_SHOTS):
            logging.debug("Fetching shot...")
            shot = await self.wtm_session.get_random_shot(require_solution=True)
            logging.debug("Got shot, putting it in the queue")
            await self.shots_queue.put(shot)

    async def guess_loop(self):
        logging.info("Starting guess loop")
        shot_number = 1
        while not self.populate_queue_task.done() or self.shots_queue.qsize() > 0:
            logging.debug("Getting shot from queue")
            self.current_shot = await self.shots_queue.get()
            logging.debug("Got shot from queue")
            logging.debug("Movie title: %s", self.current_shot.movie_title)

            self.skip_votes = set()
            self.status = GameStatus.WAITING_FOR_GUESSES
            await self.emit_signal("new_shot", shot_number=shot_number)
            self.guess_timer = asyncio.create_task(asyncio.sleep(GUESS_TIME_SECONDS))

            try:
                await self.guess_timer
            except asyncio.CancelledError:
                self.status = GameStatus.IDLE
            else:
                self.status = GameStatus.IDLE
                await self.emit_signal("shot_timeout")

            # Sleep a bit after the solution was shown to let people cool down
            await asyncio.sleep(3)

            shot_number += 1

        await self.emit_signal("game_finished")

    async def skip(self):
        await self.emit_signal("shot_skipped")
        self.guess_timer.cancel()

    async def emit_signal(self, signal_name, *args, **kwargs):
        subscribers_to_notify = self.signal_subscribers[signal_name]
        if len(subscribers_to_notify) > 0:
            asyncio.gather(
                *(subscriber(*args, **kwargs) for subscriber in subscribers_to_notify)
            )

    def subscribe_to_signal(self, signal_name, callback):
        self.signal_subscribers[signal_name].append(callback)

    async def vote_skip(self, player):
        if self.status != GameStatus.WAITING_FOR_GUESSES:
            return

        self.skip_votes.add(player)

        if len(self.skip_votes) >= len(self.scores) // 2:
            await self.skip()


class DiscordUi:
    def __init__(self, channel, game):
        self.channel = channel
        self.game = game
        self.shot_message = None

        self.game.subscribe_to_signal("shot_skipped", self.shot_skipped)
        self.game.subscribe_to_signal("game_finished", self.game_finished)
        self.game.subscribe_to_signal("shot_timeout", self.shot_timeout)
        self.game.subscribe_to_signal("new_shot", self.new_shot)
        self.game.subscribe_to_signal("correct_guess", self.correct_guess)
        self.game.subscribe_to_signal("incorrect_guess", self.incorrect_guess)

    def get_ranking(self, scores):
        ranking = [
            item
            for item in sorted(scores.items(), key=lambda item: item[1], reverse=True)
        ]
        return [
            f"{symbol} - {name} - {score} pts"
            for symbol, (name, score) in zip(["🥇", "🥈", "🥉"], ranking)
        ]

    async def correct_guess(self, player, message, movie_title):
        congrats_messages = ["yay", "correct", "nice", "good job", "👏", "you rock"]
        congrats_message = random.choice(congrats_messages)
        embed = discord.Embed(
            title=f"It was **{movie_title}** ({self.game.current_shot.movie_year})"
        )
        embed.add_field(
            name="**Leaderboard**", value="\n".join(self.get_ranking(self.game.scores)),
        )
        pts = self.game.current_combo.combo
        pts_description = "pt" if pts < 2 else "pts"
        asyncio.gather(
            message.add_reaction("✅"),
            self.channel.send(
                f"@{player} {congrats_message}! You earn **{pts} {pts_description}**. Keep scoring to use your {min(self.game.current_combo.combo + 1, MAX_COMBO)}x multiplier!",
                embed=embed,
            ),
        )

    async def incorrect_guess(self, player, guess, message):
        await message.add_reaction("❌")

    async def new_shot(self, shot_number):
        shot = self.game.current_shot
        filename = shot.image_url[shot.image_url.rfind("/") :]
        embed = discord.Embed(
            title="Guess the movie! ⬆", description="To skip it, react with ⏭.",
        )
        embed.set_footer(text=f"{shot_number} / {NB_SHOTS}")
        self.shot_message = await self.channel.send(
            embed=embed,
            files=[discord.File(fp=io.BytesIO(shot.image_data), filename=filename,)],
        )
        await self.shot_message.add_reaction("⏭")

    async def shot_timeout(self):
        await self.channel.send(
            embed=discord.Embed(
                title="Time’s up! ⌛",
                description=f"The movie was **{self.game.current_shot.movie_title}** ({self.game.current_shot.movie_year}).",
            )
        )

    async def game_finished(self):
        ranking = "\n".join(self.get_ranking(self.game.scores))
        embed = discord.Embed(
            title="Ranking", description=ranking if self.game.scores else "No scores!"
        )
        embed.add_field(
            name="About this quiz",
            value="Images courtesy of https://whatthemovie.com, go there to keep guessing!",
        )
        await self.channel.send("The movie quiz is finished!", embed=embed)

    async def shot_skipped(self):
        embed = discord.Embed(
            title="Shot skipped",
            description=f"The movie was **{self.game.current_shot.movie_title}** ({self.game.current_shot.movie_year}).",
        )
        await self.channel.send(embed=embed)


class WtmClient(discord.Client):
    def __init__(self, wtm_user, wtm_password, tmdb_token):
        super().__init__()
        self.uis = {}
        self.wtm_user = wtm_user
        self.wtm_password = wtm_password
        self.tmdb_token = tmdb_token

    def get_game(self, channel_id):
        return self.uis[channel_id].game

    async def on_ready(self):
        logger.info("Logged in as %s", self.user)

    async def start_game(self, channel, difficulty):
        try:
            game = self.get_game(channel.id)
        except KeyError:
            game = None

        if game and game.status != GameStatus.IDLE:
            logger.error(
                "Tried to start a game on channel %s, but game status is %s!",
                channel.id,
                game.status,
            )
            return

        await channel.send(
            f"Get ready, a new game is about to start in **{difficulty.value}** difficulty! Aaaaaand action! 🎬"
        )

        game = Game(
            wtm_user=self.wtm_user,
            wtm_password=self.wtm_password,
            tmdb_token=self.tmdb_token,
        )
        ui = DiscordUi(channel, game)

        self.uis[channel.id] = ui

        try:
            await game.game_loop(difficulty)
        finally:
            del self.uis[channel.id]

    async def on_reaction_add(self, reaction, user):
        if user.id == self.user.id:
            return

        try:
            ui = self.uis[reaction.message.channel.id]
        except KeyError:
            return

        if (
            ui.shot_message
            and ui.shot_message.id == reaction.message.id
            and reaction.emoji == "⏭"
        ):
            await ui.game.vote_skip(user.name)

    async def on_message(self, message):
        if message.author.id == self.user.id:
            return

        try:
            game = self.get_game(message.channel.id)
        except KeyError:
            game = None

        try:
            command = self.get_command(message)
        except ValueError as e:
            await message.channel.send(str(e))
            return

        if (
            command
            and command.type == CommandType.START
            and (not game or game.status == GameStatus.IDLE)
        ):
            if command.args:
                try:
                    difficulty = Difficulty(command.args[0])
                except ValueError:
                    await message.channel.send(
                        "Please select a valid difficulty: "
                        + ", ".join(
                            [f"**{difficulty.value}**" for difficulty in Difficulty]
                        )
                    )
                    return
            else:
                difficulty = Difficulty.EASY

            await self.start_game(message.channel, difficulty)
        elif command and command.type == CommandType.HELP:
            await message.channel.send(
                "Available commands are: start [easy|medium|hard]."
            )
        elif game and game.status == GameStatus.WAITING_FOR_GUESSES:
            await game.handle_guess(
                message.author.name, message.content, message=message
            )

    def get_command(self, message):
        mention = f"<@!{self.user.id}>"

        if not message.content.startswith(mention):
            return None

        command = message.content[len(mention) :].strip()
        try:
            command_type, *args = command.split(" ")
        except ValueError:
            command_type = command
            args = []

        return Command(type=CommandType(command_type), args=args)


def main():
    env_vars = {
        var_name: os.environ.get(var_name)
        for var_name in ("WTM_USER", "WTM_PASSWORD", "DISCORD_TOKEN", "TMDB_TOKEN")
    }
    missing_vars = {var_name for var_name, value in env_vars.items() if not value}

    if missing_vars:
        missing_vars_str = ", ".join(missing_vars)
        sys.stderr.write(
            f"The following environment variables are missing: {missing_vars_str}. Please set them and re-run the program."
        )
        sys.exit(1)

    client = WtmClient(
        wtm_user=env_vars["WTM_USER"],
        wtm_password=env_vars["WTM_PASSWORD"],
        tmdb_token=env_vars["TMDB_TOKEN"],
    )

    client.run(env_vars["DISCORD_TOKEN"])


if __name__ == "__main__":
    main()
