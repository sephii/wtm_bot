#!/usr/bin/env python
from setuptools import setup

install_requires = [
    "httpx",
    "beautifulsoup4",
    "discord.py",
]

setup(
    name="wtm_bot",
    version="1.0",
    packages=["wtm_bot"],
    package_dir={"": "src"},
    description="Movie quiz Discord bot, based on whatthemovie.com.",
    long_description="",
    author="Sylvain Fankhauser",
    author_email="sephi@fhtagn.top",
    url="https://github.com/sephii/wtm_bot",
    install_requires=[],
    license="mit",
    include_package_data=False,
    python_requires=">=3.7",
    entry_points={"console_scripts": "wtm-bot = wtm_bot.discord_bot:main"},
    classifiers=["License :: OSI Approved :: MIT License"],
)
