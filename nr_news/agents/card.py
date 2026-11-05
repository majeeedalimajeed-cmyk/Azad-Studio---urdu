"""Agent D wrapper — render and register inspected cards."""
from __future__ import annotations

from pathlib import Path

from ..config import CONFIG
from ..db import Database
from ..models import EditorialContent
from ..renderer import CardRenderer, RenderedCard
from ..util import iso, sha256_file


class CardAgent:
    def __init__(self, db: Database | None = None, renderer: CardRenderer | None = None, config=CONFIG):
        self.db = db or Database(config.db_path)
        self.renderer = renderer or CardRenderer(config)
        self.config = config

    def render(self, editorial_id: str, content: EditorialContent,
               media: list[dict] | dict | None = None) -> list[RenderedCard]:
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
            self.db.set_setting(f"card_current_layout:{card.sha256}", "true")
            photos = card.audit.get("media") or []
            reviewed = False
            if len(photos) == 1 and isinstance(photos[0], dict):
                item = photos[0]
                try:
                    reviewed = bool(all(item.get(k) for k in ("url", "license_ref", "sha256", "relevance_reviewed_by", "rights_reviewed_by", "relevance_note")) and sha256_file(item["local_path"]) == item["sha256"])
                except (KeyError, OSError):
                    reviewed = False
            if (reviewed and card.audit.get("media_count") == 1 and
                card.audit.get("photo_boxes") == [(40, 244, 1040, 504)] and
                card.audit.get("visual_inspection", {}).get("checks", {}).get("photo_1_pixels") is True):
                self.db.set_setting(f"card_reviewed_photo:{card.sha256}", "true")
            self.db.insert_api_event("agent_d", "render_card", "passed",
                                     "Card rendered with verified exact supplied monogram",
                                     details={"file": card.path, "sha256": card.sha256,
                                              "page": card.page_number, "pages": card.page_count})
        return [c for c in cards if c.visual_status == "passed"]
