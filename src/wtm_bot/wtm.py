import enum
import re
from dataclasses import dataclass
from typing import Optional

import bs4
import httpx


class Difficulty(enum.Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    ALL = "all"


@dataclass(frozen=True)
class Shot:
    image_data: bytes
    image_url: str
    movie_name: Optional[str]


def wtm_url(url):
    return f"https://whatthemovie.com{url}"


def get_parser(content):
    return bs4.BeautifulSoup(content, "html.parser")


class WtmSession:
    def __init__(self):
        self.client = httpx.AsyncClient()

    async def login(self, username, password):
        login_url = wtm_url("/user/login")
        response = await self.client.get(login_url)
        token = get_parser(response.content).select("input[name='authenticity_token']")[
            0
        ]["value"]
        response = await self.client.post(
            login_url,
            data={
                "name": username,
                "upassword": password,
                "authenticity_token": token,
                "utf8": "✓",
            },
        )

        csrf_token = get_parser(response.content).select("meta[name='csrf-token']")[0][
            "content"
        ]
        self.client.headers = {"X-CSRF-Token": csrf_token}

    async def set_difficulty(self, difficulty):
        await self.client.post(
            wtm_url("/shot/setrandomoptions"),
            data={
                "difficulty": difficulty.value,
                "keyword": "",
                "include_archive": "1",
                "include_solved": "1",
            },
        )

    async def _get_random_shot(self):
        response = await self.client.get(wtm_url("/shot/random"))
        parser = get_parser(response.content)
        image = parser.select("#still_shot")[0]["src"]
        try:
            solution_url = parser.select("#solucebutton")[0]["href"]
        except IndexError:
            solution_url = None

        solution = None
        if solution_url:
            solution_response = await self.client.get(
                wtm_url(solution_url),
                headers={
                    "Referer": str(response.url),
                    "X-CSRF-Token": parser.select("meta[name='csrf-token']")[0][
                        "content"
                    ],
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            match = re.search(
                r'setAmazonMovieName\("(.*)"\)',
                solution_response.content.decode("unicode-escape"),
            )

            if match:
                solution = match.group(1)

        r = await self.client.get(image, headers={"Referer": str(response.url)})

        return Shot(image_data=r.read(), image_url=str(image), movie_name=solution)

    async def get_random_shot(self, require_solution=False):
        shot = None

        while shot is None or (shot.movie_name is None and require_solution):
            shot = await self._get_random_shot()

        return shot
