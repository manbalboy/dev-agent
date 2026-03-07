#!/usr/bin/env python3
"""Schema-aware research search helper for search-api.manbalboy.com.

Features:
- Calls POST /search with SearchRequest payload.
- Extracts high-signal keywords from first-pass results.
- Runs limited follow-up searches using expanded keywords.
- Writes normalized JSON and Markdown context files for downstream agents.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Sequence, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest


STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "your", "you",
    "있습니다", "합니다", "그리고", "관련", "문제", "해결", "가이드", "방법", "배포", "새로고침",
    "react", "vite", "router", "blog", "news", "google",
}


@dataclass
class SearchItem:
    source: str
    title: str
    url: str
    content: str
    query: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "query": self.query,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run search-api research helper.")
    parser.add_argument("--query", required=True, help="Primary query text")
    parser.add_argument("--base-url", default=os.getenv("SEARCH_API_BASE", "https://search-api.manbalboy.com"))
    parser.add_argument("--api-key", default=os.getenv("SEARCH_API_KEY", ""))
    parser.add_argument("--display", type=int, default=4)
    parser.add_argument("--num", type=int, default=4)
    parser.add_argument("--no-blog", action="store_true")
    parser.add_argument("--no-news", action="store_true")
    parser.add_argument("--no-google", action="store_true")
    parser.add_argument("--expand-rounds", type=int, default=1)
    parser.add_argument("--top-keywords", type=int, default=5)
    parser.add_argument("--min-first-results-for-followup", type=int, default=3)
    parser.add_argument("--max-total-results", type=int, default=18)
    parser.add_argument("--json-out", default="SEARCH_RESULT.json")
    parser.add_argument("--md-out", default="SEARCH_CONTEXT.md")
    return parser.parse_args()


def post_search(
    *,
    base_url: str,
    api_key: str,
    query: str,
    display: int,
    num: int,
    blog: bool,
    news: bool,
    google: bool,
) -> List[Dict[str, str]]:
    if not api_key:
        raise RuntimeError("SEARCH_API_KEY (or --api-key) is required.")

    payload = {
        "query": query,
        "naver_blog": blog,
        "naver_news": news,
        "google": google,
        "display": display,
        "num": num,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    endpoint = base_url.rstrip("/") + "/search"
    req = urlrequest.Request(endpoint, method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    req.add_header("key", api_key)

    try:
        with urlrequest.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Search API HTTP {exc.code}: {body}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"Search API connection failed: {exc}") from exc

    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Search API returned non-JSON: {raw[:300]}") from exc

    if not isinstance(loaded, list):
        raise RuntimeError(f"Unexpected response schema (expected list): {type(loaded).__name__}")
    return [item for item in loaded if isinstance(item, dict)]


def extract_keywords(items: Sequence[SearchItem], top_n: int) -> List[str]:
    token_pattern = re.compile(r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣._#+-]{1,}")
    counter: Counter[str] = Counter()
    for item in items:
        text = f"{item.title} {item.content}"
        for token in token_pattern.findall(text):
            normalized = token.strip("._#").lower()
            if len(normalized) < 2:
                continue
            if normalized in STOPWORDS:
                continue
            if normalized.isdigit():
                continue
            counter[normalized] += 1
    return [word for word, _count in counter.most_common(max(0, top_n))]


def dedupe_by_url(items: Iterable[SearchItem]) -> List[SearchItem]:
    seen = set()
    out: List[SearchItem] = []
    for item in items:
        if item.url in seen:
            continue
        seen.add(item.url)
        out.append(item)
    return out


def cap_results(items: Sequence[SearchItem], limit: int) -> List[SearchItem]:
    """Keep top-N results while preserving query diversity."""

    if limit < 1:
        return []
    buckets: Dict[str, List[SearchItem]] = {}
    for item in items:
        buckets.setdefault(item.query, []).append(item)
    ordered_keys = list(buckets.keys())
    out: List[SearchItem] = []
    cursor = 0
    while len(out) < limit and ordered_keys:
        key = ordered_keys[cursor % len(ordered_keys)]
        bucket = buckets.get(key, [])
        if bucket:
            out.append(bucket.pop(0))
        if not bucket:
            ordered_keys = [k for k in ordered_keys if buckets.get(k)]
            cursor = 0
            continue
        cursor += 1
    return out


def build_followup_queries(base_query: str, keywords: Sequence[str], rounds: int) -> List[str]:
    if rounds < 1:
        return []
    selected = list(keywords[: max(1, rounds * 2)])
    followups: List[str] = []
    for idx in range(min(rounds, len(selected))):
        focus = selected[idx]
        followups.append(f"{base_query} {focus} 원인 해결")
    return followups


def to_items(raw_items: Sequence[Dict[str, str]], query: str) -> List[SearchItem]:
    out: List[SearchItem] = []
    for row in raw_items:
        source = str(row.get("source", "")).strip()
        title = str(row.get("title", "")).strip()
        url = str(row.get("url", "")).strip()
        content = str(row.get("content", "")).strip()
        if not (title and url):
            continue
        out.append(SearchItem(source=source, title=title, url=url, content=content, query=query))
    return out


def write_outputs(
    *,
    query: str,
    followups: Sequence[str],
    keywords: Sequence[str],
    items: Sequence[SearchItem],
    json_out: str,
    md_out: str,
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "followup_queries": list(followups),
        "keywords": list(keywords),
        "total_results": len(items),
        "results": [item.to_dict() for item in items],
    }
    with open(json_out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    lines: List[str] = [
        "# SEARCH CONTEXT",
        "",
        f"- Generated At: `{payload['generated_at']}`",
        f"- Query: `{query}`",
        f"- Follow-up Queries: `{', '.join(followups) if followups else '(none)'}`",
        f"- Extracted Keywords: `{', '.join(keywords) if keywords else '(none)'}`",
        f"- Total Results: `{len(items)}`",
        "",
        "## Top Findings",
    ]
    if not items:
        lines.append("- 검색 결과가 없습니다.")
    else:
        for idx, item in enumerate(items[:20], start=1):
            snippet = item.content.replace("\n", " ").strip()
            if len(snippet) > 180:
                snippet = snippet[:180] + "..."
            lines.extend(
                [
                    f"{idx}. **{item.title}**",
                    f"   - source: `{item.source or '-'} / query: {item.query}`",
                    f"   - url: {item.url}",
                    f"   - snippet: {snippet or '-'}",
                ]
            )
    with open(md_out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")


def main() -> int:
    args = parse_args()
    base_query = args.query.strip()
    if not base_query:
        print("query is required", file=sys.stderr)
        return 2

    all_items: List[SearchItem] = []
    first_raw = post_search(
        base_url=args.base_url,
        api_key=args.api_key,
        query=base_query,
        display=max(1, args.display),
        num=max(1, args.num),
        blog=not args.no_blog,
        news=not args.no_news,
        google=not args.no_google,
    )
    first_items = to_items(first_raw, base_query)
    all_items.extend(first_items)

    keywords = extract_keywords(first_items, top_n=max(1, args.top_keywords))
    run_followup = len(first_items) >= max(1, args.min_first_results_for_followup)
    followups = (
        build_followup_queries(base_query, keywords, rounds=max(0, args.expand_rounds))
        if run_followup
        else []
    )

    for query in followups:
        raw = post_search(
            base_url=args.base_url,
            api_key=args.api_key,
            query=query,
            display=max(1, args.display),
            num=max(1, args.num),
            blog=not args.no_blog,
            news=not args.no_news,
            google=not args.no_google,
        )
        all_items.extend(to_items(raw, query))

    deduped = dedupe_by_url(all_items)
    deduped = cap_results(deduped, limit=max(1, args.max_total_results))
    write_outputs(
        query=base_query,
        followups=followups,
        keywords=keywords,
        items=deduped,
        json_out=args.json_out,
        md_out=args.md_out,
    )
    print(f"saved_json={args.json_out}")
    print(f"saved_md={args.md_out}")
    print(f"results={len(deduped)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
