# Movie quiz Discord bot

This quiz uses data from [whatthemovie.com](https://whatthemovie.com/) to show a shot from a movie, and then
letting players guess the movie.

## Installation

To install the bot, fetch its source code (`git clone
https://github.com/sephii/wtm_bot`) and install it, preferrably in a virtual
environment (`python3 -m venv wtm_bot_venv`), using `python3 -m pip install
/path/to/wtm_bot_dir`.

You’ll also need to set the following environment variables:

* `WTM_USER`: the username of the whatthemovie.com account to use for guesses
  (this account will always have a score of 0 because the bot retrieves the
  solution instead of posting the guesses to whatthemovie.com)
* `WTM_PASSWORD`: the password of the whatthemovie.com account
* `DISCORD_TOKEN`: the token of the Discord bot. See
  https://discord.com/developers/applications for more information
  
Once this is done, eg. by exporting those environment variables, run the
`wtm-bot` command.

## Supporting

[whatthemovie.com](https://whatthemovie.com/) is an awesome website run by a few volunteers. Consider
[supporting their work](https://whatthemovie.com/page/supporter)!
