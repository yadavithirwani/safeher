"""TEST ONLY: run the API with a simulated Google (documented response shapes, fake data).

    .venv/bin/python -m tests.tools.fake_google_server --port 8766

Never use this for a demo: the "Google" places, routes and tiles it serves are made up.
"""

import argparse
import struct
import zlib

import httpx
import uvicorn

from safeher import api, google
from tests.test_google import FakeGoogle


def gray_png(size=256, shade=232) -> bytes:
    raw = b"".join(b"\x00" + bytes([shade, shade, shade]) * size for _ in range(size))
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))  # noqa: E731
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class UiFakeGoogle(FakeGoogle):
    def __call__(self, request):
        url = str(request.url)
        if "/2dtiles/" in url:
            return httpx.Response(200, content=gray_png(), headers={"content-type": "image/png"})
        if "places:autocomplete" in url:
            return httpx.Response(200, json={"suggestions": [{"placePrediction": {
                "placeId": "ChIJ_fake_lodhi_DEL", "text": {"text": "Fake Cafe, Saket"},
                "structuredFormat": {"mainText": {"text": "Fake Cafe"}, "secondaryText": {"text": "Saket, Delhi"}}}}]})
        if "streetview" in url and "metadata" not in url:
            return httpx.Response(200, content=gray_png(320, 120), headers={"content-type": "image/png"})
        return super().__call__(request)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()
    fake = UiFakeGoogle()
    client = google.GoogleClient(key="fake-key", transport=httpx.MockTransport(fake))
    google.client = lambda: client
    google.feature = lambda name: True
    uvicorn.run(api.app, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
