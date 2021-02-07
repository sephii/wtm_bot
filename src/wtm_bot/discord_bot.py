# TODO add nsfw filter (search for `div.nsfw`)
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

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class GameStatus(enum.Enum):
    IDLE = enum.auto()
    WAITING_FOR_GUESSES = enum.auto()
    LOADING = enum.auto()


class CommandType(enum.Enum):
    START = "start"
    SKIP = "skip"
    HELP = "help"


@dataclass(frozen=True)
class Command:
    type: CommandType
    args: List[str]


@dataclass(frozen=True)
class Combo:
    player: str
    combo: int


def fuzzy_compare(movie_name, guess):
    movie_name = movie_name.lower()
    guess = guess.lower()

    movie_name_parts = movie_name.split(":") + [movie_name]
    ratios = (
        difflib.SequenceMatcher(lambda x: x in " \t", movie_name_part, guess).ratio()
        for movie_name_part in movie_name_parts
    )

    return any(ratio >= 0.8 for ratio in ratios)


class Game:
    def __init__(self, wtm_user, wtm_password):
        self.wtm_user = wtm_user
        self.wtm_password = wtm_password

        self.scores = defaultdict(int)
        self.wtm_session = WtmSession()
        self.status = GameStatus.IDLE
        self.guess_timer = None
        self.shots_queue = asyncio.Queue()
        self.current_shot = None
        self.current_combo = None
        self.signal_subscribers = defaultdict(list)

    async def handle_guess(self, player, guess, **kwargs):
        if fuzzy_compare(self.current_shot.movie_name, guess):
            if self.current_combo and self.current_combo.player == player:
                self.current_combo = dataclasses.replace(
                    self.current_combo, combo=self.current_combo.combo + 1
                )
            else:
                self.current_combo = Combo(player=player, combo=1)
            self.scores[player] += self.current_combo.combo
            self.status = GameStatus.LOADING
            await self.emit_signal("correct_guess", player=player, **kwargs)
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

    async def skip(self, **kwargs):
        await self.emit_signal("shot_skipped", **kwargs)
        self.guess_timer.cancel()

    async def emit_signal(self, signal_name, *args, **kwargs):
        subscribers_to_notify = self.signal_subscribers[signal_name]
        if len(subscribers_to_notify) > 0:
            asyncio.gather(
                *(subscriber(*args, **kwargs) for subscriber in subscribers_to_notify)
            )

    def subscribe_to_signal(self, signal_name, callback):
        self.signal_subscribers[signal_name].append(callback)


class DiscordUi:
    def __init__(self, channel, game):
        self.channel = channel
        self.game = game

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

    async def correct_guess(self, player, message):
        congrats_messages = ["yay", "correct", "nice", "good job", "👏", "you rock"]
        congrats_message = random.choice(congrats_messages)
        embed = discord.Embed(title=f"It was **{self.game.current_shot.movie_name}**")
        embed.add_field(
            name="**Leaderboard**", value="\n".join(self.get_ranking(self.game.scores)),
        )
        pts = self.game.current_combo.combo
        pts_description = "pt" if pts < 2 else "pts"
        asyncio.gather(
            message.add_reaction("✅"),
            self.channel.send(
                f"@{player} {congrats_message}! You earn **{pts} {pts_description}**. Keep scoring to use your {self.game.current_combo.combo + 1}x multiplier!",
                embed=embed,
            ),
        )

    async def incorrect_guess(self, player, guess, message):
        await message.add_reaction("❌")

    async def new_shot(self, shot_number):
        shot = self.game.current_shot
        filename = shot.image_url[shot.image_url.rfind("/") :]
        embed = discord.Embed(
            title="Guess the movie! ⬆",
            description="To skip it, send `@WhatTheMovie skip`.",
        )
        embed.set_footer(text=f"{shot_number} / {NB_SHOTS}")
        await self.channel.send(
            embed=embed,
            files=[discord.File(fp=io.BytesIO(shot.image_data), filename=filename,)],
        )

    async def shot_timeout(self):
        await self.channel.send(
            embed=discord.Embed(
                title="Time’s up! ⌛",
                description=f"The movie was **{self.game.current_shot.movie_name}**.",
            )
        )

    async def game_finished(self):
        ranking = "\n".join(self.get_ranking(self.game.scores))
        embed = discord.Embed(
            title="Ranking", description=ranking if self.game.scores else "No scores!"
        )
        await self.channel.send("The movie quiz is finished!", embed=embed)

    async def shot_skipped(self, message):
        embed = discord.Embed(
            title="Shot skipped",
            description=f"The movie was **{self.game.current_shot.movie_name}**.",
        )
        asyncio.gather(message.add_reaction("👌"), self.channel.send(embed=embed))


class WtmClient(discord.Client):
    def __init__(self, wtm_user, wtm_password, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.games = {}
        self.wtm_user = wtm_user
        self.wtm_password = wtm_password

    async def on_ready(self):
        logger.info("Logged in as %s", self.user)

    async def start_game(self, channel, difficulty):
        game = self.games.get(channel.id)

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

        game = Game(wtm_user=self.wtm_user, wtm_password=self.wtm_password)
        DiscordUi(channel, game)

        self.games[channel.id] = game

        try:
            await game.game_loop(difficulty)
        finally:
            del self.games[channel.id]

    async def on_message(self, message):
        if message.author.id == self.user.id:
            return

        game = self.games.get(message.channel.id)
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
        elif (
            command
            and command.type == CommandType.SKIP
            and game
            and game.status == GameStatus.WAITING_FOR_GUESSES
        ):
            await game.skip(message=message)
        elif command and command.type == CommandType.HELP:
            await message.channel.send(
                "Available commands are: start [easy|medium|hard], skip."
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
        for var_name in ("WTM_USER", "WTM_PASSWORD", "DISCORD_TOKEN")
    }
    missing_vars = {var_name for var_name, value in env_vars.items() if not value}

    if missing_vars:
        missing_vars_str = ", ".join(missing_vars)
        sys.stderr.write(
            f"The following environment variables are missing: {missing_vars_str}. Please set them and re-run the program."
        )
        sys.exit(1)

    client = WtmClient(
        wtm_user=env_vars["WTM_USER"], wtm_password=env_vars["WTM_PASSWORD"]
    )

    client.run(env_vars["DISCORD_TOKEN"])


if __name__ == "__main__":
    main()
