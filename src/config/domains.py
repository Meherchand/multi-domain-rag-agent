"""Display metadata for knowledge domains.

The UIs need labels, groupings and suggested questions. None of that belongs in
code: a fork replaces the corpus, and it should be able to replace the labels
with it. So the metadata lives in ``data/domains.json`` next to the corpus, and
every field is optional — a domain with no entry falls back to a title-cased
version of its folder name.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.config.settings import settings

logger = logging.getLogger(__name__)

METADATA_FILENAME = "domains.json"


@dataclass
class DomainMeta:
    key: str
    label: str
    description: str = ""
    faqs: list[str] = field(default_factory=list)


@dataclass
class DomainCatalog:
    domains: dict[str, DomainMeta] = field(default_factory=dict)
    groups: list[dict[str, object]] = field(default_factory=list)

    def label(self, key: str) -> str:
        meta = self.domains.get(key)
        return meta.label if meta else key.replace("_", " ").title()

    def faqs(self, key: str) -> list[str]:
        meta = self.domains.get(key)
        return list(meta.faqs) if meta else []

    def description(self, key: str) -> str:
        meta = self.domains.get(key)
        return meta.description if meta else ""

    def grouped(self, available: list[str]) -> list[dict[str, object]]:
        """Group the available domains for a picker.

        Domains present on disk but absent from every configured group are
        collected into an "Other" group, so adding a corpus folder is enough to
        make it appear in the UI.
        """
        grouped: list[dict[str, object]] = []
        claimed = set()

        for group in self.groups:
            members = [d for d in group.get("domains", []) if d in available]
            if members:
                grouped.append({"name": group.get("name", "Group"), "domains": members})
                claimed.update(members)

        remaining = [d for d in available if d not in claimed]
        if remaining:
            grouped.append({"name": "Other" if grouped else "Knowledge domains", "domains": remaining})
        return grouped


def load_catalog(data_dir: Path | None = None) -> DomainCatalog:
    path = Path(data_dir or settings.data_dir) / METADATA_FILENAME
    if not path.is_file():
        logger.info("No %s found; using derived domain labels", METADATA_FILENAME)
        return DomainCatalog()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s (%s); using derived labels", METADATA_FILENAME, type(exc).__name__)
        return DomainCatalog()

    domains = {
        key: DomainMeta(
            key=key,
            label=value.get("label", key.replace("_", " ").title()),
            description=value.get("description", ""),
            faqs=list(value.get("faqs", [])),
        )
        for key, value in (raw.get("domains") or {}).items()
        if isinstance(value, dict)
    }
    return DomainCatalog(domains=domains, groups=list(raw.get("groups") or []))


catalog = load_catalog()
