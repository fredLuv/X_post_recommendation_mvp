from __future__ import annotations

import argparse

from sqlalchemy import select

from x_trend_idea_mvp.database import SessionLocal
from x_trend_idea_mvp.models import AudienceProfile, TrackedQuery


PRESETS: dict[str, dict[str, object]] = {
    "ai-builders": {
        "niche": "AI builders and startup operators",
        "description": "Find explainable product and workflow shifts in AI, agents, and developer tooling.",
        "preferred_formats": ["short_thread", "single_post"],
        "queries": [
            "\"ai agents\" OR \"agent workflows\"",
            "\"developer tools\" OR \"devtools\"",
            "\"internal tooling\" OR \"ops automation\"",
        ],
    },
    "fintech-builders": {
        "niche": "Fintech founders, operators, and builders",
        "description": "Track payments, compliance, infrastructure, and distribution conversations.",
        "preferred_formats": ["short_thread", "single_post"],
        "queries": [
            "fintech OR payments OR \"banking as a service\"",
            "\"fraud prevention\" OR compliance OR underwriting",
            "\"embedded finance\" OR \"payment orchestration\"",
        ],
    },
    "b2b-saas": {
        "niche": "B2B SaaS founders and GTM teams",
        "description": "Track pricing, sales, retention, onboarding, and product-led growth shifts.",
        "preferred_formats": ["short_thread", "single_post"],
        "queries": [
            "\"product led growth\" OR PLG",
            "SaaS AND pricing",
            "\"sales led\" OR onboarding OR retention",
        ],
    },
    "crypto-signals": {
        "niche": "Crypto builders, researchers, and market infrastructure operators",
        "description": "Track high-signal crypto conversations across protocols, exchanges, infrastructure, and research accounts.",
        "preferred_formats": ["short_thread", "single_post"],
        "queries": [
            "from:solana OR from:ethereum OR from:base",
            "from:coinbase OR from:binance OR from:krakenfx",
            "from:chainlink OR from:a16zcrypto",
            "from:MessariCrypto OR from:tokenterminal OR from:DefiLlama",
            "from:VitalikButerin",
        ],
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed an audience profile and starter X queries.")
    parser.add_argument("--preset", choices=sorted(PRESETS), required=True)
    parser.add_argument("--name", help="Audience display name. Defaults to the preset name.")
    args = parser.parse_args()

    preset = PRESETS[args.preset]
    audience_name = args.name or args.preset

    with SessionLocal() as session:
        audience = session.scalar(select(AudienceProfile).where(AudienceProfile.name == audience_name))
        if audience is None:
            audience = AudienceProfile(
                name=audience_name,
                niche=preset["niche"],
                description=preset["description"],
                preferred_formats=preset["preferred_formats"],
            )
            session.add(audience)
            session.flush()

        existing_queries = {
            row.query
            for row in session.scalars(
                select(TrackedQuery).where(TrackedQuery.audience_profile_id == audience.id)
            )
        }
        inserted_queries = 0
        for query in preset["queries"]:
            if query in existing_queries:
                continue
            session.add(
                TrackedQuery(
                    audience_profile_id=audience.id,
                    query=query,
                    kind="keyword",
                    active=True,
                )
            )
            inserted_queries += 1

        session.commit()
        print(
            {
                "audience_id": audience.id,
                "audience_name": audience.name,
                "inserted_queries": inserted_queries,
                "preset": args.preset,
            }
        )


if __name__ == "__main__":
    main()
