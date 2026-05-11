"""Sample Python source for the happy fixture."""

import httpx
from fastapi import FastAPI

app = FastAPI()


def fetch() -> httpx.Response:
    return httpx.get("https://example.com")
