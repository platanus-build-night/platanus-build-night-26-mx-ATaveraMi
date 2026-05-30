"""CLI del scraper. Lee la lista de desarrolladoras, corre el pipeline con
concurrencia limitada y escribe data/scraped.json (shape de seed.json).

Uso:
    python -m scraper.run --limit 10
    python -m scraper.run --only vinte.com.mx skyhaus.mx
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from .config import settings
from .pipeline import scrape_developer


def _normalize(site: str) -> str:
    site = site.strip()
    if not site.startswith("http"):
        site = "https://" + site
    return site


def load_sites(path: str, limit: int | None) -> list[str]:
    lines = [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    sites = [_normalize(l) for l in lines]
    return sites[:limit] if limit else sites


async def run(sites: list[str], output: str) -> dict:
    sem = asyncio.Semaphore(settings.concurrency)

    async with httpx.AsyncClient() as client:
        # Hard cap por sitio: site_budget + margen para 1 request en vuelo.
        hard_cap = settings.site_budget + settings.request_timeout + 5

        async def worker(site: str) -> dict:
            async with sem:
                try:
                    r = await asyncio.wait_for(scrape_developer(client, site), timeout=hard_cap)
                except asyncio.TimeoutError:
                    r = {"website": site, "extraction_status": "failed",
                         "extraction_error": f"hard timeout (> {hard_cap:.0f}s)", "developments": []}
                except Exception as e:  # noqa: BLE001
                    r = {"website": site, "extraction_status": "failed",
                         "extraction_error": str(e), "developments": []}
                ndev = len(r.get("developments", []))
                nmod = sum(len(d["models"]) for d in r.get("developments", []))
                print(f"  [{r.get('extraction_status'):<7}] {site} — {ndev} desarrollos, {nmod} modelos")
                return r

        results = await asyncio.gather(*[worker(s) for s in sites])

    summary = {
        "developers": len(results),
        "done": sum(1 for r in results if r.get("extraction_status") == "done"),
        "skipped": sum(1 for r in results if r.get("extraction_status") == "skipped"),
        "failed": sum(1 for r in results if r.get("extraction_status") == "failed"),
        "developments": sum(len(r.get("developments", [])) for r in results),
        "models": sum(len(d["models"]) for r in results for d in r.get("developments", [])),
    }
    out = {"_meta": summary, "developers": results}
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Scraper de vivienda nueva (Viviendin)")
    ap.add_argument("--input", default="data/desarrolladoras.txt")
    ap.add_argument("--output", default=settings.output_path)
    ap.add_argument("--limit", type=int, default=None, help="primeras N desarrolladoras")
    ap.add_argument("--only", nargs="*", help="dominios/URLs específicos a scrapear")
    args = ap.parse_args()

    if not settings.gemini_api_key:
        raise SystemExit("Falta GEMINI_API_KEY (ponla en .env). Ver .env.example")

    sites = [_normalize(s) for s in args.only] if args.only else load_sites(args.input, args.limit)
    print(f"Scrapeando {len(sites)} desarrolladoras (concurrencia={settings.concurrency})...\n")

    summary = asyncio.run(run(sites, args.output))
    print(f"\n✓ {args.output}")
    print(f"  done={summary['done']} skipped={summary['skipped']} failed={summary['failed']} "
          f"| {summary['developments']} desarrollos, {summary['models']} modelos")


if __name__ == "__main__":
    main()
