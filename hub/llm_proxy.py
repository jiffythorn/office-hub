"""
hub/llm_proxy.py - the hub's AI fallback chain, exposed OpenAI-compatible.

One endpoint (default :8085/v1) that every AI consumer in the hub shares -
the chat UI, the Telegram/Discord bot, anything - with tiered fallback:

  1. local      llama.cpp on this machine          (privacy modes 1-2 only)
  2. nokey      pollinations.ai - free, NO key      (privacy mode 2 only)
  3. keyedfree  Groq / OpenRouter free tiers        (key via env var)
  4. paid       any OpenAI-compatible paid API      (key via env var)

Rules:
  - privacy_mode 1 (retrieval_only): chain = local only. Nothing ever leaves.
  - privacy_mode 2 (local):          chain = local -> nokey -> keyedfree -> paid.
  - privacy_mode 3 (cloud):          chain = keyedfree -> paid (no local model).
  - A tier is skipped automatically when its key is missing.
  - Tiers can be force-disabled in config.json -> llm_chain.disabled.
"""

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_cfg():
    return json.loads((ROOT / "config.json").read_text())


# ---------------------------------------------------------------------------
# tier definitions
# ---------------------------------------------------------------------------

def build_chain(cfg):
    """Return [(tier_name, base_url, api_key, model, note)] honoring privacy."""
    mode = cfg.get("privacy_mode", "retrieval_only")
    chain_cfg = cfg.get("llm_chain", {})
    disabled = set(chain_cfg.get("disabled", []))
    chain = []

    if mode in ("local", "retrieval_only"):
        la = cfg.get("local_ai", {})
        chain.append(("local", f"http://{la.get('host','127.0.0.1')}:{la.get('port',8082)}/v1",
                      "local", la.get("model", "local-qwen"), "on this machine, offline"))

    if mode == "local" and "nokey" not in disabled:
        chain.append(("nokey", "https://text.pollinations.ai/openai",
                      "", chain_cfg.get("nokey_model", "openai"),
                      "free, no key (pollinations.ai)"))

    kf_key = os.environ.get("HUB_KEYEDFREE_API_KEY", "")
    if kf_key and "keyedfree" not in disabled:
        base = chain_cfg.get("keyedfree_base", "https://api.groq.com/openai/v1")
        chain.append(("keyedfree", base, kf_key,
                      chain_cfg.get("keyedfree_model", "llama-3.1-8b-instant"),
                      "free tier with key"))

    paid_key = os.environ.get("HUB_PAID_API_KEY", "")
    if paid_key and "paid" not in disabled:
        base = chain_cfg.get("paid_base", "https://api.openai.com/v1")
        chain.append(("paid", base, paid_key,
                      chain_cfg.get("paid_model", "gpt-4o-mini"),
                      "paid API"))
    return chain


def _post_chat(base_url, api_key, model, messages, max_tokens, temperature, timeout):
    payload = json.dumps({"model": model, "messages": messages,
                          "max_tokens": max_tokens, "temperature": temperature}).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                                 data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return content.strip(), (data.get("model") or model)


def chat(messages, max_tokens=700, temperature=0.3, timeout=180):
    """Try each tier in order; return (text, tier_used, model). Raises on total failure."""
    cfg = load_cfg()
    errors = []
    for tier, base, key, model, note in build_chain(cfg):
        try:
            text, real_model = _post_chat(base, key, model, messages,
                                          max_tokens, temperature, timeout)
            if text:
                return text, tier, real_model
            errors.append(f"{tier}: empty reply")
        except Exception as e:
            msg = str(e)[:120]
            errors.append(f"{tier}: {msg}")
            print(f"[llm-chain] {tier} failed ({note}): {msg}")
    raise RuntimeError("All AI tiers failed: " + " | ".join(errors))


# ---------------------------------------------------------------------------
# FastAPI app (mountable / standalone)
# ---------------------------------------------------------------------------

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Office Hub LLM Chain", docs_url=None, redoc_url=None)


class Msg(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    messages: list[Msg]
    max_tokens: int = 700
    temperature: float = 0.3


@app.get("/health")
def health():
    cfg = load_cfg()
    return {"ok": True, "privacy_mode": cfg.get("privacy_mode"),
            "chain": [{"tier": t, "model": m, "note": n} for t, _b, _k, m, n in build_chain(cfg)]}


@app.post("/v1/chat/completions")
def completions(body: ChatIn):
    try:
        text, tier, model = chat([m.model_dump() for m in body.messages],
                                 body.max_tokens, body.temperature)
        return {"choices": [{"message": {"role": "assistant", "content": text}}],
                "model": model, "hub_tier": tier}
    except RuntimeError as e:
        return {"error": str(e)}


def main():
    import uvicorn
    cfg = load_cfg()
    host = cfg.get("llm_chain", {}).get("host", "127.0.0.1")
    port = cfg.get("llm_chain", {}).get("port", 8085)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
