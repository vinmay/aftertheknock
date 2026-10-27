"""Model backends. The live path is a local Ollama server with JSON-schema output."""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import urllib.request
from dataclasses import dataclass

OLLAMA_URL = os.environ.get("KNOCK_OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("KNOCK_MODEL", "gemma4:e2b")


@dataclass
class Reply:
    text: str
    latency_s: float
    prompt_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    def to_dict(self) -> dict:
        return {"text": self.text, "latency_s": round(self.latency_s, 3),
                "prompt_tokens": self.prompt_tokens, "output_tokens": self.output_tokens,
                "model": self.model}


def prompt_hash(messages: list[dict], schema: dict | None) -> str:
    blob = json.dumps({"m": messages, "s": schema}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


class OllamaModel:
    live = True

    def __init__(self, name: str = DEFAULT_MODEL, url: str = OLLAMA_URL,
                 temperature: float = 0.8, num_ctx: int = 2048, timeout: float = 120):
        self.name, self.url = name, url.rstrip("/")
        self.temperature, self.num_ctx, self.timeout = temperature, num_ctx, timeout

    def info(self) -> dict:
        req = urllib.request.Request(f"{self.url}/api/show", data=json.dumps({"model": self.name}).encode())
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        det = d.get("details", {})
        return {"model": self.name, "quantization": det.get("quantization_level"),
                "parameter_size": det.get("parameter_size"), "family": det.get("family"),
                "digest": self._digest()}

    def _digest(self) -> str | None:
        with urllib.request.urlopen(f"{self.url}/api/tags", timeout=30) as r:
            for m in json.load(r).get("models", []):
                if m["name"] == self.name:
                    return m["digest"][:12]
        return None

    def generate(self, messages: list[dict], schema: dict | None = None, seed: int = 0,
                 max_tokens: int = 200) -> Reply:
        body = {"model": self.name, "messages": messages, "stream": False, "think": False,
                "keep_alive": "30m",
                "options": {"seed": seed, "temperature": self.temperature,
                            "num_ctx": self.num_ctx, "num_predict": max_tokens}}
        if schema is not None:
            body["format"] = schema
        req = urllib.request.Request(f"{self.url}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            d = json.load(r)
        dt = time.perf_counter() - t0
        return Reply(d["message"]["content"], dt, d.get("prompt_eval_count", 0),
                     d.get("eval_count", 0), self.name)


class ScriptedModel:
    """Returns queued replies in order. For tests and deliberate-failure evals."""
    live = False
    name = "scripted"

    def __init__(self, replies: list):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def info(self) -> dict:
        return {"model": self.name, "quantization": None}

    def generate(self, messages, schema=None, seed=0, max_tokens=200) -> Reply:
        self.calls.append(messages)
        if not self.replies:
            return Reply("{}", 0.0, model=self.name)
        r = self.replies.pop(0)
        return Reply(r if isinstance(r, str) else json.dumps(r), 0.0, model=self.name)


class RandomModel:
    """Offline stand-in: picks schema-valid values at random. Not used in the live path."""
    live = False
    name = "random"

    def info(self) -> dict:
        return {"model": self.name, "quantization": None}

    def generate(self, messages, schema=None, seed=0, max_tokens=200) -> Reply:
        rng = random.Random(f"{seed}:{prompt_hash(messages, schema)}")
        if schema is None:
            return Reply("The storm leans on the windows.", 0.0, model=self.name)
        out = {}
        for k, spec in schema["properties"].items():
            if "enum" in spec:
                out[k] = rng.choice(spec["enum"])
            elif spec.get("pattern") == "^[0-9]{4}$":
                out[k] = f"{rng.randrange(10000):04d}"
            else:
                out[k] = rng.choice(["", "Don't.", "You already let me in.", "It's cold down there."])
        return Reply(json.dumps(out), 0.0, model=self.name)
