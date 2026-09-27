"""Public-data provenance registry + market reference seed.

This module documents where *real* public procurement / commodity data comes
from, with licenses and access metadata. Records are created by
``scripts/process_dataset.py`` (fetch or offline skip).

The ``market_seed`` rows are **indicative** series descriptors, never used as
ground truth: no benchmark answer depends on public market figures. Synthetic
supplier prices are anchored on the synthetic catalogue prices in
``backend/app/dataset/catalog.py`` so synthetic and real data never mix.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

DATASET_ROOT = Path(__file__).resolve().parents[3] / "data"

#: registry of legally-usable public datasets (verified via web research 2026-09)
PROVENANCE: list[dict] = [
    {
        "id": "ted-csv",
        "name": "Tenders Electronic Daily (TED) CSV subset",
        "publisher": "Publications Office of the European Union",
        "type": "procurement_notices",
        "distribution": "CSV per year; contract notices + award notices + VEAT",
        "url": "https://data.europa.eu/data/datasets/ted-csv",
        "access_url": "https://data.europa.eu/api/hub/store?uri=https://data.europa.eu/88u/dataset/ted-csv",
        "license": "EUPL / CC BY 4.0 (re-use via Publications Office open data)",
        "license_url": "https://data.europa.eu/data/datasets/ted-csv",
        "coverage": "2006-01-01 .. current",
        "notes": "Fields: who bought what from whom, how much, award criteria. Re-use conditions published on the dataset page.",
    },
    {
        "id": "ted-open-data-service",
        "name": "TED Open Data Service (Linked Open Data / ePO)",
        "publisher": "Publications Office of the European Union",
        "type": "procurement_notices",
        "distribution": "SPARQL endpoint, RDF/JSON/CSV",
        "url": "https://data.ted.europa.eu/",
        "access_url": "https://data.ted.europa.eu/",
        "license": "Re-use allowed under EU open data policy (attribution requested)",
        "license_url": "https://data.ted.europa.eu/",
        "coverage": "2012-01-01 .. current",
        "notes": "Knowledge graph of TED notices; export any SPARQL result to CSV.",
    },
    {
        "id": "contracsfinder",
        "name": "UK Contracts Finder (Open Contracting OCDS)",
        "publisher": "Crown Commercial Service / UK Government",
        "type": "award_history",
        "distribution": "JSON/CSV/Excel per year",
        "url": "https://data.open-contracting.org/en/publication/128",
        "access_url": "https://data.open-contracting.org/en/publication/128",
        "license": "Open Government Licence v3.0 (OGL)",
        "license_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
        "coverage": "2016 .. current",
        "notes": "Contracts above £10k central gov / £25k wider public sector. Fully reusable with attribution.",
    },
    {
        "id": "usaspending",
        "name": "USAspending.gov (FPDS federal contract data)",
        "publisher": "U.S. Department of the Treasury",
        "type": "award_history",
        "distribution": "CSV/ZIP downloads + JSON REST API (no auth)",
        "url": "https://www.usaspending.gov/",
        "access_url": "https://api.usaspending.gov/api/v2/download/awards/",
        "license": "U.S. Government work (public domain)",
        "license_url": "https://www.usaspending.gov/",
        "coverage": "2000 .. current",
        "notes": "Historical contract awards (FPDS-NG); transaction-level bulk download.",
    },
    {
        "id": "opentender",
        "name": "OpenTender (TED re-publication)",
        "publisher": "Government Transparency Institute",
        "type": "procurement_notices",
        "distribution": "OCDS JSON/CSV per year",
        "url": "https://opentender.eu/",
        "access_url": "https://opentender.eu/eu/download",
        "license": "CC BY-NC-SA 4.0 (non-commercial)",
        "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
        "coverage": "2006 .. current",
        "notes": "35 jurisdictions; clean normalized OCDS. NON-COMMERCIAL — exclude from commercial reuse.",
    },
    {
        "id": "wb-pink-sheet",
        "name": "World Bank Commodity Price Data (The Pink Sheet)",
        "publisher": "The World Bank",
        "type": "commodity_prices",
        "distribution": "XLSX/PDF monthly; monthly & annual averages",
        "url": "https://www.worldbank.org/en/research/commodity-markets",
        "access_url": "https://thedocs.worldbank.org/en/doc/18675f1d1639c7a34d463f59263ba0a2-0050012025/",
        "license": "CC BY 4.0",
        "license_url": "https://datacatalog.worldbank.org/public-licenses#cc-by",
        "coverage": "1960 .. current",
        "notes": "Metals (aluminium, copper, steel, zinc…), energy, agriculture USD spot benchmarks. Use as market-context anchors, never as benchmark answers.",
    },
]


#: indicative series descriptors for market-context tasks (values left NULL;
#: populated by scripts/process_dataset.py when the Pink Sheet is fetched)
MARKET_SEED = [
    {"series": "Aluminium (LME), cash, USD/mt", "unit": "USD/mt", "year_avg": None},
    {"series": "Copper (LME), grade A, USD/mt", "unit": "USD/mt", "year_avg": None},
    {"series": "Steel, HRC, China, USD/mt", "unit": "USD/mt", "year_avg": None},
    {"series": "Zinc (LME), high grade, USD/mt", "unit": "USD/mt", "year_avg": None},
    {"series": "Iron ore, 62% Fe, USD/dmt", "unit": "USD/dmt", "year_avg": None},
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_provenance() -> Path:
    target = DATASET_ROOT / "raw" / "PROVENANCE.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset": "procurement-benchmark",
        "synthetic": "all cases and suppliers are fictional and clearly flagged",
        "recorded_at": now_iso(),
        "sources": PROVENANCE,
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def write_market_seed(path: Path | None = None) -> Path:
    target = path or DATASET_ROOT / "processed" / "reference" / "market_prices.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "description": "Indicative market series from the World Bank Pink Sheet (CC BY 4.0). "
                       "Values are populated by scripts/process_dataset.py when fetched. "
                       "Not used as benchmark ground truth.",
        "license": "CC BY 4.0",
        "source_provenance_id": "wb-pink-sheet",
        "recorded_at": now_iso(),
        "series": MARKET_SEED,
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def public_seed_ids() -> list[str]:
    """Provenance ids referenced by generated cases (for attribution)."""
    return ["wb-pink-sheet", "ted-open-data-service"]