"""Agent D wrapper — render and register inspected cards."""
from __future__ import annotations

from pathlib import Path

from ..config import CONFIG
from ..db import Database
from ..models import EditorialContent
from ..renderer import CardRenderer, RenderedCard
from ..util import iso


class CardAgent:
    def __init__(self, db: Database | None = None, renderer: CardRenderer | None = None, config=CONFIG):
        self.db = db or Database(config.db_path)
        self.renderer = renderer or CardRenderer(config)
        self.config = config

    def render(self, editorial_id: str, content: EditorialContent, media: dict | None = None) -> list[RenderedCard]:
        stem = f"{iso()[:10]}-{editorial_id[:12]}"
        cards = self.renderer.render_cards(content, stem, media=media)
        for card in cards:
            if card.visual_status != "passed":
                self.db.insert_api_event("agent_d", "visual_inspection", "failed",
                                         "Card failed deterministic visual checks",
                                         details=card.audit["visual_inspection"])
                continue
            self.db.insert_card(
                editorial_id, card.path, card.sha256, card.width, card.height,
                card.page_number, card.page_count, card.visual_status,
            )
            self.db.insert_api_event("agent_d", "render_card", "passed",
                                     "Card rendered with verified exact supplied monogram",
                                     details={"file": card.path, "sha256": card.sha256,
                                              "page": card.page_number, "pages": card.page_count})
        return [c for c in cards if c.visual_status == "passed"]
