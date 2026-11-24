"""Pull contract addresses, cashtags and topic tags out of free text."""
from __future__ import annotations

import html
import re

B58 = "1-9A-HJ-NP-Za-km-z"
SOL_RE = re.compile(rf"(?<![{B58}])[{B58}]{{32,44}}(?![{B58}])")
EVM_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
CASHTAG_RE = re.compile(r"(?<![\w$])\$([A-Za-z][A-Za-z0-9]{1,9})\b")
TAG_RE = re.compile(r"<[^>]+>")

# Addresses that show up everywhere and are never "the coin being called".
IGNORE = {
    "So11111111111111111111111111111111111111112", "11111111111111111111111111111111",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "0x4200000000000000000000000000000000000006", "0x0000000000000000000000000000000000000000",
}

TOPICS = {
    "listing": ["will list", "lists ", "listing", "new listing", "launchpool", "launchpad", "perpetual",
                "adds support", "now available", "trading opens", "roadmap", "上线", "상장"],
    "hack": ["hack", "exploit", "drained", "attack", "stolen", "vulnerability", "compromised"],
    "rug": ["rug", "rugged", "scam", "honeypot", "exit scam", "dumped"],
    "regulation": ["sec ", "lawsuit", "etf", "regulat", "ban ", "cftc", "court"],
    "solana": ["solana", "pump.fun", "raydium", "jupiter", "phantom"],
    "base": ["base chain", "on base", "coinbase", "clanker", "zora"],
    "meme": ["memecoin", "meme coin", "meme token", "degen"],
    "whale": ["whale", "smart money", "insider", "bought", "accumulat"],
}


def clean(text: str) -> str:
    return html.unescape(TAG_RE.sub(" ", text or "")).replace("<wbr>", "")


def solana_addresses(text: str) -> set[str]:
    out = set()
    for m in SOL_RE.findall(text):
        # Real addresses mix cases and digits; plain long words don't.
        if m in IGNORE or not (any(c.isdigit() for c in m) or (m != m.lower() and m != m.upper())):
            continue
        if sum(c.isdigit() for c in m) == 0 and sum(c.isupper() for c in m) < 4:
            continue
        out.add(m)
    return out


def evm_addresses(text: str) -> set[str]:
    return {m.lower() for m in EVM_RE.findall(text) if m.lower() not in IGNORE}


def cashtags(text: str) -> set[str]:
    return {t.upper() for t in CASHTAG_RE.findall(text)}


def topics(text: str) -> set[str]:
    low = " " + text.lower() + " "
    return {name for name, words in TOPICS.items() if any(w in low for w in words)}
