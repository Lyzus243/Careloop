import json
import secrets

DEFAULT_STAGES = ["Received", "In progress", "Ready", "Collected"]
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_tracking_code() -> str:
    return "CL-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))


def parse_stages(raw) -> list:
    if raw:
        try:
            data = json.loads(raw)
            stages = [str(s).strip()[:60] for s in data if str(s).strip()]
            if stages:
                return stages[:12]
        except (ValueError, TypeError):
            pass
    return list(DEFAULT_STAGES)


def current_stage(stages: list, stage) -> str:
    if stage in stages:
        return stage
    return stages[0]


def next_stage(stages: list, stage):
    cur = current_stage(stages, stage)
    i = stages.index(cur)
    return stages[i + 1] if i + 1 < len(stages) else None


import os


def public_base_url() -> str:
    base = (os.getenv("FRONTEND_URL") or "").strip().rstrip("/")
    if not base or "vercel.app" in base:
        base = "https://mycareloop.com.ng"
    return base


def build_message(business: str, customer_name: str, code: str, stage: str) -> str:
    first = (customer_name or "").strip().split(" ")[0] or "there"
    link = public_base_url() + "/track/" + code
    return (
        "Hi " + first + ", update from " + (business or "us")
        + ": your order is now at \"" + stage + "\". "
        + "Track it anytime: " + link + " (code " + code + ")"
    )
