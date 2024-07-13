import asyncio
import enum
import re
import urllib.parse
from dataclasses import dataclass
from typing import List, Optional

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
    movie_title: Optional[str]
    movie_alternative_titles: List[str]
    movie_year: Optional[int]


js_unicode_re = re.compile(r"\\u([0-9a-f]{4})")
tmdb_base_url = "https://api.themoviedb.org/3"


def wtm_url(url):
    return f"https://whatthemovie.com{url}"


def get_parser(content):
    return bs4.BeautifulSoup(content, "html.parser")


def unescape_js_unicode(match):
    return chr(int(match.group(1), 16))


class TmdbClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.client = httpx.AsyncClient()

    async def get_movie_name(self, movie_id, lang):
        url = tmdb_base_url + f"/movie/{movie_id}"

        try:
            response = await self.client.get(
                url, params={"language": lang, "api_key": self.api_key}
            )
        except httpx.ReadTimeout:
            return None

        if response.status_code != 200:
            return None

        try:
            return response.json()["title"]
        except KeyError:
            return None

    async def get_alternative_titles(self, title, year):
        url = tmdb_base_url + "/search/movie"

        try:
            response = await self.client.get(
                url,
                params={"api_key": self.api_key, "query": title, "year": year},
            )
        except httpx.ReadTimeout:
            return set()

        if response.status_code != 200:
            return set()

        try:
            response_data = response.json()
            if not response_data["results"]:
                return set()
        except (ValueError, KeyError):
            return set()

        movie_id = response_data["results"][0]["id"]
        titles = await asyncio.gather(
            *[self.get_movie_name(movie_id, lang) for lang in ("fr-FR", "en-US")]
        )
        return set(title for title in titles if title)


class WtmSession:
    def __init__(self, tmdb_token):
        self.client = httpx.AsyncClient()
        self.tmdb_client = TmdbClient(tmdb_token)

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
            follow_redirects=True,
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

    async def _get_random_shot(self, nsfw_ok, exclude_tags=None):
        shot = None

        while not shot:
            response = await self.client.get(
                wtm_url("/shot/random"), follow_redirects=True
            )
            parser = get_parser(response.content)
            image = parser.select("#still_shot")[0]["src"]
            try:
                solution_url = parser.select("#solucebutton")[0]["href"]
            except IndexError:
                solution_url = None

            nsfw = len(parser.select("div.nsfw")) > 0
            if nsfw and not nsfw_ok:
                continue

            if exclude_tags:
                tags = {
                    element.text for element in parser.select("#shot_tag_list li a")
                }
                if tags & exclude_tags:
                    continue

            title = None
            year = None
            alternative_titles = set()
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
                solution_code = js_unicode_re.sub(
                    unescape_js_unicode, solution_response.content.decode()
                )
                title_match = re.search(
                    r'setAmazonMovieName\((["\'])(?P<title>.*)\1\)', solution_code
                )
                year_match = re.search(r"<strong>.+\((\d+)\)</strong>", solution_code)

                if title_match and year_match:
                    title = urllib.parse.unquote_plus(
                        title_match.group("title").strip()
                    )
                    year = int(year_match.group(1))
                    alternative_titles = await self.tmdb_client.get_alternative_titles(
                        title, year
                    ) - {title}

            r = await self.client.get(image, headers={"Referer": str(response.url)})
            shot = Shot(
                image_data=r.read(),
                image_url=str(image),
                movie_title=title,
                movie_alternative_titles=alternative_titles,
                movie_year=year,
            )

        return shot

    async def get_random_shot(self, require_solution=False):
        shot = None

        while shot is None or (not shot.movie_title and require_solution):
            shot = await self._get_random_shot(
                nsfw_ok=False, exclude_tags={"nude", "nudity", "boob", "boobs"}
            )

        return shot
