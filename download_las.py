#!/usr/bin/env python3
"""
Download every .las file listed in a web directory index (e.g. PASDA Philly LiDAR).

Usage:
    python download_las.py /path/to/output_dir
    python download_las.py /path/to/output_dir --url <listing_url> --workers 4
    python download_las.py /path/to/output_dir --dry-run      # list files + total size only

Re-running is safe: files already present at the correct size are skipped,
and partial downloads (*.part) are discarded and retried.
"""
import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, unquote

import requests

DEFAULT_URL = "https://www.pasda.psu.edu/download/phillyLiDAR/2022/LAS/Classified_LAS/"
CHUNK = 1024 * 1024  # 1 MB


def list_las(session, url):
    """Return sorted absolute URLs of all .las links on the listing page."""
    html = session.get(url, timeout=60).text
    hrefs = re.findall(r'href=["\']([^"\']+\.las)["\']', html, flags=re.IGNORECASE)
    return sorted({urljoin(url, h) for h in hrefs})


def remote_size(session, url):
    r = session.head(url, timeout=60, allow_redirects=True)
    return int(r.headers.get("Content-Length", -1))


def download(session, url, out_dir, retries=3):
    name = unquote(url.rsplit("/", 1)[-1])
    dest = out_dir / name
    size = remote_size(session, url)

    if dest.exists() and (size < 0 or dest.stat().st_size == size):
        return f"skip  {name}"

    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            with session.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(CHUNK):
                        f.write(chunk)
            if size >= 0 and tmp.stat().st_size != size:
                raise IOError(f"size mismatch ({tmp.stat().st_size} != {size})")
            tmp.replace(dest)
            return f"done  {name}  ({size / 1e6:,.0f} MB)"
        except Exception as e:
            if attempt == retries:
                tmp.unlink(missing_ok=True)
                return f"FAIL  {name}: {e}"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("out_dir", type=Path, help="local directory to save files into")
    p.add_argument("--url", default=DEFAULT_URL, help="directory listing URL")
    p.add_argument("--workers", type=int, default=2, help="parallel downloads (be polite: 2-4)")
    p.add_argument("--dry-run", action="store_true", help="list files and total size, download nothing")
    args = p.parse_args()

    session = requests.Session()
    session.headers["User-Agent"] = "las-downloader/1.0"

    urls = list_las(session, args.url)
    if not urls:
        sys.exit("No .las links found at " + args.url)
    print(f"Found {len(urls)} .las files")

    if args.dry_run:
        total = sum(max(remote_size(session, u), 0) for u in urls)
        for u in urls:
            print(u)
        print(f"Total size: {total / 1e9:,.1f} GB")
        return

    args.out_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(download, session, u, args.out_dir) for u in urls]
        for i, fut in enumerate(as_completed(futures), 1):
            msg = fut.result()
            failed += msg.startswith("FAIL")
            print(f"[{i}/{len(urls)}] {msg}", flush=True)

    print(f"Finished. {failed} failed." if failed else "Finished. All files OK.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
