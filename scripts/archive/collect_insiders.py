"""SEC Form 4 insider purchases and sales for the S&P 500, 2018 onward. Resumable.

    python scripts/collect_insiders.py
    python scripts/collect_insiders.py --from 2024q1

READ-ONLY against SEC EDGAR's official Insider Transactions Data Sets.
Writes data/insiders/form4.csv; raw zips are not kept (about 14 MB a quarter).

POINT-IN-TIME KEY: FILING_DATE, the day the trade became public -- not
TRANS_DATE, which can be days earlier and was not knowable then. Every
backtest must join on filing_date.

KEPT: Form 4 non-derivative transactions with code P (open-market purchase)
or S (open-market sale). Grants, option exercises and tax withholding are
excluded -- they are compensation mechanics, not decisions to buy or sell.

    filing_date, trans_date, symbol, owner, role, code, shares, price, value

SEC requires a User-Agent naming a contact; rate is far below its 10/s limit.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASES = ("https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/",
         "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/")
OUT = Path("data/insiders/form4.csv")
DONE = Path("data/insiders/quarters_done.txt")
HEADERS = {"User-Agent": "stock-scanner research Kittickds@icloud.com"}
COLUMNS = ["filing_date", "trans_date", "symbol", "owner", "role", "code", "shares", "price", "value"]


def universe() -> set[str]:
    with open("data/constituents.csv", encoding="utf-8") as handle:
        return {r["Symbol"].strip() for r in csv.DictReader(handle)}


def quarters(start: str):
    year, q = int(start[:4]), int(start[-1])
    today = date.today()
    while (year, q) <= (today.year, (today.month - 1) // 3 + 1):
        yield f"{year}q{q}"
        year, q = (year + 1, 1) if q == 4 else (year, q + 1)


def iso(text: str) -> str:
    try:
        return datetime.strptime(text.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return ""


def tsv(z: zipfile.ZipFile, name: str):
    with z.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace"),
                                  delimiter="\t")


def parse(blob: bytes, wanted: set[str]) -> list[list]:
    z = zipfile.ZipFile(io.BytesIO(blob))
    subs = {}
    for r in tsv(z, "SUBMISSION.tsv"):
        sym = (r.get("ISSUERTRADINGSYMBOL") or "").strip().upper()
        if r.get("DOCUMENT_TYPE", "").strip() in ("4", "4/A") and sym in wanted:
            subs[r["ACCESSION_NUMBER"]] = (iso(r["FILING_DATE"]), sym)
    owners = {}
    for r in tsv(z, "REPORTINGOWNER.tsv"):
        if r["ACCESSION_NUMBER"] in subs and r["ACCESSION_NUMBER"] not in owners:
            role = (r.get("RPTOWNER_RELATIONSHIP") or "").strip()
            title = (r.get("RPTOWNER_TITLE") or "").strip()
            owners[r["ACCESSION_NUMBER"]] = ((r.get("RPTOWNERNAME") or "").strip(),
                                             f"{role}{': ' + title if title else ''}")
    rows = []
    for r in tsv(z, "NONDERIV_TRANS.tsv"):
        acc, code = r["ACCESSION_NUMBER"], (r.get("TRANS_CODE") or "").strip()
        if acc not in subs or code not in ("P", "S"):
            continue
        try:
            shares = float(r.get("TRANS_SHARES") or 0)
            price = float(r.get("TRANS_PRICEPERSHARE") or 0)
        except ValueError:
            continue
        filed, sym = subs[acc]
        owner, role = owners.get(acc, ("", ""))
        rows.append([filed, iso(r.get("TRANS_DATE") or ""), sym, owner, role, code,
                     round(shares), round(price, 4), round(shares * price, 2)])
    return rows


def main(start: str) -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = set(DONE.read_text().split()) if DONE.exists() else set()
    wanted = universe()
    todo = [q for q in quarters(start) if q not in done]
    print(f"{len(todo)} quarter(s) to fetch for {len(wanted)} symbols ({len(done)} done)", flush=True)
    with httpx.Client(headers=HEADERS, timeout=180, follow_redirects=True) as client:
        for q in todo:
            blob = None
            for base in BASES:
                r = client.get(f"{base}{q}_form345.zip")
                if r.status_code == 200 and r.content[:2] == b"PK":
                    blob = r.content
                    break
            if blob is None:
                print(f"  {q}: not published yet")
                continue
            rows = parse(blob, wanted)
            new = not OUT.exists()
            with OUT.open("a", newline="", encoding="utf-8") as handle:
                w = csv.writer(handle)
                if new:
                    w.writerow(COLUMNS)
                w.writerows(rows)
            with DONE.open("a") as handle:
                handle.write(q + "\n")
            buys = sum(1 for x in rows if x[5] == "P")
            print(f"  {q}: {len(rows)} trades kept ({buys} purchases)", flush=True)
            time.sleep(1.0)
    print(f"done -> {OUT}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="start", default="2018q1")
    raise SystemExit(main(p.parse_args().start))
