from __future__ import annotations

import asyncio
import argparse
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from x_trend_idea_mvp.constants import DEFAULT_FIXTURE_LOOKBACK_DAYS


NUMERIC_LINE_RE = re.compile(r"^\d+(?:\.\d+)?[KMB]?$", re.IGNORECASE)
STATUS_PATH_RE = re.compile(r"^/([^/]+)/status/(\d+)")


@dataclass
class ExtractedPost:
    id: str
    author_handle: str
    text: str
    created_at: str
    lang: str
    like_count: int
    reply_count: int
    repost_count: int
    quote_count: int
    impression_count: int | None
    url: str
    tracked_query_id: str
    author_id: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract visible public X profile posts into fixture JSON."
    )
    parser.add_argument(
        "--handle",
        action="append",
        dest="handles",
        help="X handle to extract. Can be repeated.",
    )
    parser.add_argument(
        "--handles-file",
        help="JSON file with either {'accounts': [{'handle': '...'}]} or a raw list of handles.",
    )
    parser.add_argument("--output", required=True, help="Path to write fixture JSON.")
    parser.add_argument(
        "--tracked-query-id",
        default="REPLACE_QUERY_ID",
        help="Tracked query id to attach to each extracted post.",
    )
    parser.add_argument(
        "--posts-per-handle",
        type=int,
        default=5,
        help="Maximum visible posts to capture per handle.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_FIXTURE_LOOKBACK_DAYS,
        help="Keep only posts whose normalized timestamps fall within this many days.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Number of profile pages to fetch concurrently.",
    )
    parser.add_argument(
        "--scroll-rounds",
        type=int,
        default=4,
        help="Number of incremental scroll passes per profile.",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Run the browser in headed mode for debugging.",
    )
    return parser.parse_args()


def main() -> None:
    asyncio.run(async_main())


async def async_main() -> None:
    args = parse_args()
    handles = load_handles(args.handles, args.handles_file)
    if not handles:
        raise SystemExit("Provide at least one --handle or a --handles-file.")

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise SystemExit(
            "playwright is required for public profile extraction. "
            "Install it and run `playwright install chromium` first."
        ) from exc

    captured_at = datetime.now(tz=UTC)
    extracted_posts: list[dict] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not args.headful)
        semaphore = asyncio.Semaphore(max(1, args.concurrency))

        async def extract_handle(handle: str) -> list[dict]:
            async with semaphore:
                page = await browser.new_page()
                try:
                    url = f"https://x.com/{handle}"
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                        await page.wait_for_timeout(2_000)
                    except PlaywrightTimeoutError:
                        return []
                    handle_posts: list[dict] = []
                    seen_post_ids: set[str] = set()
                    prior_height = -1
                    for _ in range(max(1, args.scroll_rounds)):
                        articles = await page.locator("article").evaluate_all(
                            """(nodes) => {
                                return nodes.map((node) => {
                                  const timeAnchor = node.querySelector('time')?.closest('a');
                                  return {
                                    text: node.innerText,
                                    statusHref: timeAnchor?.getAttribute('href') || null,
                                  };
                                });
                            }"""
                        )

                        for article in articles:
                            parsed = parse_article(
                                profile_handle=handle,
                                article_text=article["text"],
                                status_href=article["statusHref"],
                                captured_at=captured_at,
                                tracked_query_id=args.tracked_query_id,
                                lookback_days=args.lookback_days,
                            )
                            if parsed is None or parsed.id in seen_post_ids:
                                continue
                            seen_post_ids.add(parsed.id)
                            handle_posts.append(parsed.__dict__)
                            if len(handle_posts) >= args.posts_per_handle:
                                return handle_posts

                        current_height = await page.evaluate("document.body.scrollHeight")
                        if current_height == prior_height:
                            break
                        prior_height = current_height
                        await page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 1.5, 1200))")
                        await page.wait_for_timeout(1_500)
                    return handle_posts
                finally:
                    await page.close()

        results = await asyncio.gather(*(extract_handle(handle) for handle in handles))
        for items in results:
            extracted_posts.extend(items)

        await browser.close()

    payload = {
        "source": {
            "type": "public_profile_extractor",
            "platform": "x",
            "captured_at": captured_at.isoformat(),
            "handles": handles,
            "lookback_days": args.lookback_days,
            "posts_per_handle": args.posts_per_handle,
            "scroll_rounds": args.scroll_rounds,
            "notes": [
                "Extracted from visible public X profile pages using browser automation.",
                "This is snapshot data, not full API coverage.",
                "Relative timestamps were normalized at extraction time.",
            ],
        },
        "posts": deduplicate_posts(extracted_posts),
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2))
    print({"output": str(output_path), "handles": len(handles), "posts": len(payload["posts"])})


def load_handles(handles: list[str] | None, handles_file: str | None) -> list[str]:
    collected = list(handles or [])
    if handles_file:
        payload = json.loads(Path(handles_file).read_text())
        if isinstance(payload, dict) and "accounts" in payload:
            collected.extend(account["handle"] for account in payload["accounts"])
        elif isinstance(payload, list):
            collected.extend(str(item) for item in payload)
        else:
            raise SystemExit("Unsupported handles file format.")
    return sorted({handle.lstrip("@") for handle in collected})


def parse_article(
    *,
    profile_handle: str,
    article_text: str,
    status_href: str | None,
    captured_at: datetime,
    tracked_query_id: str,
    lookback_days: int,
) -> ExtractedPost | None:
    status_match = STATUS_PATH_RE.match(status_href or "")
    if status_match is None:
        return None

    status_handle = status_match.group(1)
    status_id = status_match.group(2)
    if status_handle.lower() != profile_handle.lower():
        return None
    lines = normalize_lines(article_text)
    if not lines:
        return None

    author_handle, timestamp_label = extract_author_and_time(lines, status_handle)
    if timestamp_label is None:
        return None
    created_at = parse_timestamp_label(timestamp_label, captured_at)
    if created_at < captured_at - timedelta(days=lookback_days):
        return None
    metrics = extract_metrics(lines)
    body = extract_body(lines, author_handle, timestamp_label)
    if not body:
        return None

    return ExtractedPost(
        id=status_id,
        author_handle=author_handle or profile_handle,
        text=body,
        created_at=created_at.isoformat().replace("+00:00", "Z"),
        lang="en",
        like_count=metrics["like_count"],
        reply_count=metrics["reply_count"],
        repost_count=metrics["repost_count"],
        quote_count=0,
        impression_count=metrics["impression_count"],
        url=f"https://x.com/{status_handle}/status/{status_id}",
        tracked_query_id=tracked_query_id,
    )


def normalize_lines(article_text: str) -> list[str]:
    return [line.strip() for line in article_text.splitlines() if line.strip()]


def extract_author_and_time(lines: list[str], fallback_handle: str) -> tuple[str, str | None]:
    author_handle = fallback_handle
    timestamp_label = None
    for index, line in enumerate(lines):
        if line.startswith("@"):
            author_handle = line.lstrip("@")
            if index + 2 < len(lines) and lines[index + 1] == "·":
                timestamp_label = lines[index + 2]
            elif index + 1 < len(lines):
                timestamp_label = lines[index + 1]
            break
    return author_handle, timestamp_label


def parse_timestamp_label(label: str, captured_at: datetime) -> datetime:
    lowered = label.lower()
    if lowered.endswith("h") and lowered[:-1].isdigit():
        return captured_at - timedelta(hours=int(lowered[:-1]))
    if lowered.endswith("m") and lowered[:-1].isdigit():
        return captured_at - timedelta(minutes=int(lowered[:-1]))
    if lowered.endswith("s") and lowered[:-1].isdigit():
        return captured_at - timedelta(seconds=int(lowered[:-1]))
    if re.match(r"^[A-Z][a-z]{2} \d{1,2}, \d{4}$", label):
        return datetime.strptime(label, "%b %d, %Y").replace(tzinfo=UTC)

    current_year = captured_at.year
    month_day_match = re.match(r"^([A-Z][a-z]{2}) (\d{1,2})$", label)
    if month_day_match:
        month = month_day_match.group(1)
        day = int(month_day_match.group(2))
        parsed = datetime.strptime(f"{month} {day} {current_year}", "%b %d %Y").replace(tzinfo=UTC)
        if parsed > captured_at + timedelta(days=1):
            parsed = parsed.replace(year=current_year - 1)
        return parsed
    return captured_at


def extract_metrics(lines: list[str]) -> dict[str, int | None]:
    trailing = []
    for line in reversed(lines):
        if NUMERIC_LINE_RE.match(line):
            trailing.append(line)
            continue
        if trailing:
            break
    trailing = list(reversed(trailing))
    reply_count = parse_metric(trailing[0]) if len(trailing) >= 1 else 0
    repost_count = parse_metric(trailing[1]) if len(trailing) >= 2 else 0
    like_count = parse_metric(trailing[2]) if len(trailing) >= 3 else 0
    impression_count = parse_metric(trailing[3]) if len(trailing) >= 4 else None
    return {
        "reply_count": reply_count,
        "repost_count": repost_count,
        "like_count": like_count,
        "impression_count": impression_count,
    }


def parse_metric(value: str) -> int:
    multiplier = 1
    normalized = value.upper()
    if normalized.endswith("K"):
        multiplier = 1_000
        normalized = normalized[:-1]
    elif normalized.endswith("M"):
        multiplier = 1_000_000
        normalized = normalized[:-1]
    elif normalized.endswith("B"):
        multiplier = 1_000_000_000
        normalized = normalized[:-1]
    return int(float(normalized) * multiplier)


def extract_body(lines: list[str], author_handle: str, timestamp_label: str) -> str:
    start_index = 0
    for index, line in enumerate(lines):
        if line == f"@{author_handle}" and index + 2 < len(lines):
            if lines[index + 1] == "·" and lines[index + 2] == timestamp_label:
                start_index = index + 3
                break

    content_lines: list[str] = []
    for line in lines[start_index:]:
        if NUMERIC_LINE_RE.match(line):
            break
        if line in {"GIF", "Image", "Pinned", "Article", "Show more"}:
            continue
        content_lines.append(line)

    if content_lines and content_lines[0].endswith(" reposted"):
        content_lines = content_lines[1:]
    return " ".join(content_lines).strip()


def deduplicate_posts(posts: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for post in posts:
        seen[post["id"]] = post
    return list(seen.values())


if __name__ == "__main__":
    main()
