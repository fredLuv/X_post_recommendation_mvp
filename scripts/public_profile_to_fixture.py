from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from x_trend_idea_mvp.constants import DEFAULT_FIXTURE_LOOKBACK_DAYS


STATUS_PATH_RE = re.compile(r"^/([^/]+)/status/(\d+)")
URL_RE = re.compile(r"https?://\S+")
WHITESPACE_RE = re.compile(r"\s+")
TRIVIAL_TEXT_RE = re.compile(r"^(?:quote|replying to|show more|pinned)$", re.IGNORECASE)


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


@dataclass
class HandleDiagnostics:
    handle: str
    page_loaded: bool = False
    load_error: str | None = None
    articles_seen: int = 0
    authored_candidates_seen: int = 0
    parsed_posts: int = 0
    duplicate_posts: int = 0
    scroll_rounds_completed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def record_skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract visible public X profile posts into fixture JSON.")
    parser.add_argument("--handle", action="append", dest="handles", help="X handle to extract. Can be repeated.")
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
    diagnostics: list[HandleDiagnostics] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not args.headful)
        semaphore = asyncio.Semaphore(max(1, args.concurrency))

        async def extract_handle(handle: str) -> tuple[list[dict], HandleDiagnostics]:
            async with semaphore:
                page = await browser.new_page()
                diagnostic = HandleDiagnostics(handle=handle)
                try:
                    url = f"https://x.com/{handle}"
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                        await page.wait_for_timeout(2_000)
                        diagnostic.page_loaded = True
                    except PlaywrightTimeoutError:
                        diagnostic.load_error = "timeout"
                        return [], diagnostic

                    handle_posts: list[dict] = []
                    seen_post_ids: set[str] = set()
                    prior_height = -1

                    for round_index in range(max(1, args.scroll_rounds)):
                        articles = await extract_articles(page)
                        diagnostic.articles_seen += len(articles)

                        for article in articles:
                            diagnostic.authored_candidates_seen += count_matching_statuses(handle, article.get("status_hrefs", []))
                            parsed, reason = parse_article_data(
                                profile_handle=handle,
                                article=article,
                                captured_at=captured_at,
                                tracked_query_id=args.tracked_query_id,
                                lookback_days=args.lookback_days,
                            )
                            if parsed is None:
                                diagnostic.record_skip(reason or "parse_failed")
                                continue
                            if parsed.id in seen_post_ids:
                                diagnostic.duplicate_posts += 1
                                continue
                            seen_post_ids.add(parsed.id)
                            handle_posts.append(parsed.__dict__)
                            diagnostic.parsed_posts += 1
                            if len(handle_posts) >= args.posts_per_handle:
                                diagnostic.scroll_rounds_completed = round_index + 1
                                return handle_posts, diagnostic

                        current_height = await page.evaluate("document.body.scrollHeight")
                        diagnostic.scroll_rounds_completed = round_index + 1
                        if current_height == prior_height:
                            break
                        prior_height = current_height
                        await page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 1.8, 1600))")
                        await page.wait_for_timeout(1_800)

                    return handle_posts, diagnostic
                finally:
                    await page.close()

        results = await asyncio.gather(*(extract_handle(handle) for handle in handles))
        for items, diagnostic in results:
            extracted_posts.extend(items)
            diagnostics.append(diagnostic)

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
                "Structured DOM extraction is used where possible before text fallback.",
                "Relative timestamps were normalized at extraction time.",
            ],
        },
        "diagnostics": [asdict(item) for item in diagnostics],
        "posts": deduplicate_posts(extracted_posts),
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2))
    print(
        {
            "output": str(output_path),
            "handles": len(handles),
            "posts": len(payload["posts"]),
            "diagnostics": summarize_diagnostics(diagnostics),
        }
    )


async def extract_articles(page) -> list[dict]:
    return await page.locator("article").evaluate_all(
        """(nodes) => {
            const extractMetric = (node, testIds) => {
              for (const testId of testIds) {
                const metricNode = node.querySelector(`[data-testid="${testId}"] [dir="ltr"]`);
                const metricText = metricNode?.textContent?.trim();
                if (metricText) return metricText;
              }
              return null;
            };

            return nodes.map((node) => {
              const timeNode = node.querySelector("time");
              const timeAnchor = timeNode?.closest("a");
              const statusHrefs = Array.from(node.querySelectorAll('a[href*="/status/"]'))
                .map((link) => link.getAttribute("href"))
                .filter(Boolean);
              const textBlocks = Array.from(node.querySelectorAll('[data-testid="tweetText"]'))
                .map((el) => el.innerText.trim())
                .filter(Boolean);

              return {
                text_blocks: textBlocks,
                article_text: node.innerText,
                time_href: timeAnchor?.getAttribute("href") || null,
                time_label: timeNode?.textContent?.trim() || null,
                time_datetime: timeNode?.getAttribute("datetime") || null,
                status_hrefs: statusHrefs,
                reply_count: extractMetric(node, ["reply"]),
                repost_count: extractMetric(node, ["retweet", "unretweet"]),
                like_count: extractMetric(node, ["like", "unlike"]),
                quote_count: extractMetric(node, ["quote"]),
                impression_count: extractMetric(node, ["analytics"]),
                lang: node.querySelector('[lang]')?.getAttribute("lang") || "en",
                pinned: node.innerText.includes("Pinned"),
              };
            });
        }"""
    )


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


def parse_article_data(
    *,
    profile_handle: str,
    article: dict,
    captured_at: datetime,
    tracked_query_id: str,
    lookback_days: int,
) -> tuple[ExtractedPost | None, str | None]:
    status_href = select_authored_status_href(profile_handle, article)
    if status_href is None:
        return None, "no_authored_status"

    status_match = STATUS_PATH_RE.match(status_href)
    if status_match is None:
        return None, "bad_status_href"

    status_handle = status_match.group(1)
    status_id = status_match.group(2)

    created_at = parse_article_timestamp(article, captured_at)
    if created_at is None:
        return None, "no_timestamp"
    if created_at < captured_at - timedelta(days=lookback_days):
        return None, "outside_lookback"

    body = extract_structured_body(article)
    if not body:
        body = extract_body_fallback(article.get("article_text", ""))
    if not body:
        return None, "empty_body"

    return (
        ExtractedPost(
            id=status_id,
            author_handle=status_handle or profile_handle,
            text=body,
            created_at=created_at.isoformat().replace("+00:00", "Z"),
            lang=article.get("lang") or "en",
            like_count=parse_metric(article.get("like_count")),
            reply_count=parse_metric(article.get("reply_count")),
            repost_count=parse_metric(article.get("repost_count")),
            quote_count=parse_metric(article.get("quote_count")),
            impression_count=parse_optional_metric(article.get("impression_count")),
            url=f"https://x.com/{status_handle}/status/{status_id}",
            tracked_query_id=tracked_query_id,
        ),
        None,
    )


def select_authored_status_href(profile_handle: str, article: dict) -> str | None:
    profile_handle = profile_handle.lower()
    time_href = article.get("time_href")
    if matches_profile_status(profile_handle, time_href):
        return time_href

    for href in article.get("status_hrefs", []):
        if matches_profile_status(profile_handle, href):
            return href
    return None


def matches_profile_status(profile_handle: str, href: str | None) -> bool:
    if href is None:
        return False
    match = STATUS_PATH_RE.match(href)
    if match is None:
        return False
    return match.group(1).lower() == profile_handle


def count_matching_statuses(profile_handle: str, status_hrefs: list[str]) -> int:
    profile_handle = profile_handle.lower()
    return sum(1 for href in status_hrefs if matches_profile_status(profile_handle, href))


def parse_article_timestamp(article: dict, captured_at: datetime) -> datetime | None:
    time_datetime = article.get("time_datetime")
    if time_datetime:
        try:
            return datetime.fromisoformat(str(time_datetime).replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            pass

    time_label = article.get("time_label")
    if not time_label:
        return None
    return parse_timestamp_label(str(time_label), captured_at)


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


def extract_structured_body(article: dict) -> str:
    text_blocks = [normalize_text_block(block) for block in article.get("text_blocks", [])]
    text_blocks = [block for block in text_blocks if block]
    if not text_blocks:
        return ""
    return text_blocks[0]


def extract_body_fallback(article_text: str) -> str:
    lines = [line.strip() for line in article_text.splitlines() if line.strip()]
    content_lines: list[str] = []
    for line in lines:
        if TRIVIAL_TEXT_RE.match(line):
            continue
        if parse_optional_metric(line) is not None:
            continue
        content_lines.append(line)

    if content_lines and content_lines[0].endswith(" reposted"):
        content_lines = content_lines[1:]
    joined = " ".join(content_lines)
    return normalize_text_block(joined)


def normalize_text_block(value: str) -> str:
    value = URL_RE.sub(" ", value)
    value = WHITESPACE_RE.sub(" ", value).strip()
    if not value or TRIVIAL_TEXT_RE.match(value):
        return ""
    return value


def parse_metric(value: str | None) -> int:
    parsed = parse_optional_metric(value)
    return parsed or 0


def parse_optional_metric(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = str(value).strip().upper().replace(",", "")
    if not normalized:
        return None

    multiplier = 1
    if normalized.endswith("K"):
        multiplier = 1_000
        normalized = normalized[:-1]
    elif normalized.endswith("M"):
        multiplier = 1_000_000
        normalized = normalized[:-1]
    elif normalized.endswith("B"):
        multiplier = 1_000_000_000
        normalized = normalized[:-1]

    try:
        return int(float(normalized) * multiplier)
    except ValueError:
        return None


def deduplicate_posts(posts: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for post in posts:
        existing = seen.get(post["id"])
        if existing is None or len(post.get("text", "")) > len(existing.get("text", "")):
            seen[post["id"]] = post
    return list(seen.values())


def summarize_diagnostics(items: list[HandleDiagnostics]) -> dict[str, int]:
    loaded = sum(1 for item in items if item.page_loaded)
    with_posts = sum(1 for item in items if item.parsed_posts > 0)
    return {
        "loaded_handles": loaded,
        "handles_with_posts": with_posts,
        "handles_without_posts": len(items) - with_posts,
    }


if __name__ == "__main__":
    main()
