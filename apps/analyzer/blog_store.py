"""S3-backed store for the satellite blog network (replaces the Neon "blog" DB).

Layout on the shared bucket (the ``signalor-backlink-engine`` prefix is public-read):
  <PREFIX>/<folder>/index.json   → array of post summaries for a site
  <PREFIX>/<folder>/<slug>.json  → the full post (incl. content_html)

The 5 satellite sites read the public HTTPS URLs directly (no SDK); Signalor
reads/writes via boto3. Folder names match the categories the user created.
"""

import json
import os
import time
from functools import lru_cache

# BlogPost.Site value -> S3 folder name (hyphenated, as created in the bucket).
SITE_FOLDERS = {
    "research": "research",
    "listicals": "listicals",
    "market_trends": "market-trends",
    "comparison": "comparison",
    "step_guide": "step-guide",
}


def _cfg() -> dict:
    return {
        "key": os.getenv("BACKLINKS_BLOG_AWS_ACCESS_KEY_ID", ""),
        "secret": os.getenv("BACKLINKS_BLOG_AWS_SECRET_ACCESS_KEY", ""),
        "region": os.getenv("BACKLINKS_BLOG_AWS_REGION", "ap-south-1"),
        "bucket": os.getenv("BACKLINKS_BLOG_AWS_BUCKET", ""),
        "prefix": (os.getenv("BACKLINKS_BLOG_AWS_PREFIX", "signalor-backlink-engine") or "").strip("/"),
    }


@lru_cache(maxsize=4)
def _client_cached(key: str, secret: str, region: str):
    import boto3

    return boto3.client(
        "s3", region_name=region, aws_access_key_id=key, aws_secret_access_key=secret
    )


def _client():
    c = _cfg()
    return _client_cached(c["key"], c["secret"], c["region"]), c


def _folder(site: str) -> str:
    return SITE_FOLDERS.get(site, site)


def public_url(site: str, slug: str) -> str:
    """Public HTTPS URL of the rendered post on the satellite site is built from
    SATELLITE_SITES; this is the raw S3 object URL (used by the sites to fetch)."""
    c = _cfg()
    return f"https://{c['bucket']}.s3.{c['region']}.amazonaws.com/{c['prefix']}/{_folder(site)}/{slug}.json"


def _post_key(c, site, slug):
    return f"{c['prefix']}/{_folder(site)}/{slug}.json"


def _index_key(c, site):
    return f"{c['prefix']}/{_folder(site)}/index.json"


def _get_json(s3, bucket, key, default):
    from botocore.exceptions import ClientError

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404", "NotFound"):
            return default
        raise


def _put_json(s3, bucket, key, data):
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, default=str).encode("utf-8"),
        ContentType="application/json",
        CacheControl="public, max-age=60",
    )


_SUMMARY_FIELDS = (
    "id",
    "site",
    "slug",
    "title",
    "description",
    "image_url",
    "brand_url",
    "brand_ref",
    "source",
    "status",
    "published_at",
    "created_at",
)


def _summary(p: dict) -> dict:
    return {k: p.get(k) for k in _SUMMARY_FIELDS}


def new_id() -> int:
    return int(time.time() * 1000)


def list_index(site: str) -> list:
    """Post summaries for one site (from its index.json)."""
    s3, c = _client()
    return _get_json(s3, c["bucket"], _index_key(c, site), [])


def slug_exists(site: str, slug: str) -> bool:
    return any(p.get("slug") == slug for p in list_index(site))


def put_post(post: dict) -> dict:
    """Write the full post object and upsert its summary into the site index."""
    s3, c = _client()
    site, slug = post["site"], post["slug"]
    _put_json(s3, c["bucket"], _post_key(c, site, slug), post)
    idx = [p for p in _get_json(s3, c["bucket"], _index_key(c, site), []) if p.get("slug") != slug]
    idx.append(_summary(post))
    idx.sort(key=lambda p: (p.get("published_at") or p.get("created_at") or ""), reverse=True)
    _put_json(s3, c["bucket"], _index_key(c, site), idx)
    return post


def get_post(site: str, slug: str):
    s3, c = _client()
    return _get_json(s3, c["bucket"], _post_key(c, site, slug), None)


def update_post(site: str, slug: str, fields: dict):
    post = get_post(site, slug)
    if not post:
        return None
    for k, v in fields.items():
        if v is not None:
            post[k] = v
    return put_post(post)


def delete_post(site: str, slug: str) -> bool:
    s3, c = _client()
    s3.delete_object(Bucket=c["bucket"], Key=_post_key(c, site, slug))
    idx = [p for p in _get_json(s3, c["bucket"], _index_key(c, site), []) if p.get("slug") != slug]
    _put_json(s3, c["bucket"], _index_key(c, site), idx)
    return True


def list_for_brand(brand_ref: str) -> list:
    """All posts (summaries) for a brand, across every site, newest first."""
    out = []
    for site in SITE_FOLDERS:
        for p in list_index(site):
            if p.get("brand_ref") == brand_ref:
                out.append(p)
    out.sort(key=lambda p: (p.get("published_at") or p.get("created_at") or ""), reverse=True)
    return out
