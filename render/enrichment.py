#!/usr/bin/env python3
import csv
import io
import json
import os
import queue
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import uuid
from urllib.error import HTTPError, URLError

BACKEND = "http://127.0.0.1:8080"
UA = "ExpoRadar-Enrichment/1.0"
IMAGE_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "ico"}
LIGHTWEIGHT_MODE = os.getenv("EXPO_RADAR_MAPS_LIGHTWEIGHT", "true").strip().lower() not in {"0", "false", "no", "off"}
MAX_DEPTH = max(1, min(10, int(os.getenv("EXPO_RADAR_MAPS_MAX_DEPTH", "3"))))
MAX_CANDIDATES = max(1, min(25, int(os.getenv("EXPO_RADAR_MAPS_MAX_CANDIDATES", "10"))))
JOB_TTL_SECONDS = max(300, int(os.getenv("EXPO_RADAR_MAPS_JOB_TTL_SECONDS", "1800")))

RAW_FIELDS = [
    "input_id", "link", "title", "category", "address", "open_hours",
    "popular_times", "website", "phone", "plus_code", "review_count",
    "review_rating", "reviews_per_rating", "latitude", "longitude", "cid",
    "status", "descriptions", "reviews_link", "thumbnail", "timezone",
    "price_range", "data_id", "street_view_url", "place_id", "images",
    "reservations", "order_online", "menu", "owner", "complete_address",
    "credit_cards_accepted", "about", "user_reviews", "user_reviews_extended",
    "emails",
]


def _fold(value):
    value = value or ""
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _tokens(value):
    return {t for t in _fold(value).split() if len(t) > 1}


def _jsonish(value, fallback=None):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback if fallback is not None else value


def _to_int(value):
    try:
        return int(float(value))
    except Exception:
        return 0


def _to_float(value):
    try:
        return float(value)
    except Exception:
        return None


def _clean_emails(value):
    if not value:
        return []
    found = []
    for candidate in re.split(r"[\s,;]+", value):
        candidate = candidate.strip().strip("<>()[]{}'\"")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z0-9-]{2,}", candidate):
            continue
        tld = candidate.rsplit(".", 1)[-1].lower()
        if tld in IMAGE_TLDS:
            continue
        if candidate.lower() not in {x.lower() for x in found}:
            found.append(candidate)
    return found


def _domain(url):
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return ""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _name_score(query_name, candidate_name):
    q = _fold(query_name)
    c = _fold(candidate_name)
    if not q or not c:
        return 0

    # Commercial names often vary only by spacing/punctuation:
    # "Power Dent" vs "PowerDent", "MIX-PAC" vs "MIXPAC".
    q_compact = q.replace(" ", "")
    c_compact = c.replace(" ", "")

    if q == c or q_compact == c_compact:
        return 60
    if (
        c.startswith(q)
        or q.startswith(c)
        or c_compact.startswith(q_compact)
        or q_compact.startswith(c_compact)
    ):
        return 56
    if q in c or c in q or q_compact in c_compact or c_compact in q_compact:
        return 52
    qt, ct = _tokens(q), _tokens(c)
    if not qt or not ct:
        return 0
    overlap = len(qt & ct)
    union = len(qt | ct)
    jaccard = overlap / union if union else 0
    return round(45 * jaccard)


def _location_score(city, state, address, complete_address):
    score = 0
    city_fold = _fold(city)
    state_fold = _fold(state)
    address_fold = _fold(address)
    parsed_city = _fold((complete_address or {}).get("city", ""))
    parsed_state = _fold((complete_address or {}).get("state", ""))
    if city_fold and (city_fold in address_fold or city_fold == parsed_city):
        score += 9
    if state_fold and (state_fold in address_fold or state_fold == parsed_state):
        score += 6
    return score


def normalize_row(row, query):
    complete_address = _jsonish(row.get("complete_address"), {}) or {}
    owner = _jsonish(row.get("owner"), {}) or {}
    emails = _clean_emails(row.get("emails", ""))

    normalized = {
        "name": (row.get("title") or "").strip(),
        "category": (row.get("category") or "").strip(),
        "website": (row.get("website") or "").strip(),
        "domain": _domain(row.get("website") or ""),
        "phone": (row.get("phone") or "").strip(),
        "emails": emails,
        "address": (row.get("address") or "").strip(),
        "complete_address": complete_address,
        "latitude": _to_float(row.get("latitude")),
        "longitude": _to_float(row.get("longitude")),
        "rating": _to_float(row.get("review_rating")),
        "review_count": _to_int(row.get("review_count")),
        "google_maps_url": (row.get("link") or "").strip(),
        "place_id": (row.get("place_id") or "").strip(),
        "cid": (row.get("cid") or "").strip(),
        "owner": owner,
        "status": (row.get("status") or "").strip(),
    }

    name_points = _name_score(query["company_name"], normalized["name"])
    location_points = _location_score(
        query.get("city", ""),
        query.get("state", ""),
        normalized["address"],
        complete_address,
    )

    completeness = 0
    if normalized["website"]:
        completeness += 7
    if normalized["phone"]:
        completeness += 6
    if normalized["emails"]:
        completeness += 7

    review_points = 0
    if normalized["review_count"] >= 100:
        review_points = 5
    elif normalized["review_count"] >= 20:
        review_points = 4
    elif normalized["review_count"] >= 5:
        review_points = 3
    elif normalized["review_count"] > 0:
        review_points = 1

    score = min(100, name_points + location_points + completeness + review_points)
    normalized["match_score"] = score
    normalized["score_breakdown"] = {
        "name": name_points,
        "location": location_points,
        "completeness": completeness,
        "reviews": review_points,
    }
    if LIGHTWEIGHT_MODE:
        # Keep only compact source evidence in memory/JSON. Heavy fields such as
        # images, reviews and open-hours are unnecessary for ExpoRadar discovery.
        keep = {
            "input_id", "link", "title", "category", "address", "website",
            "phone", "review_count", "review_rating", "latitude", "longitude",
            "cid", "status", "place_id", "owner", "complete_address", "emails",
        }
        normalized["raw"] = {field: row.get(field, "") for field in RAW_FIELDS if field in keep}
    else:
        normalized["raw"] = {field: row.get(field, "") for field in RAW_FIELDS}
    return normalized


def dedupe_and_rank(rows, query):
    candidates = [normalize_row(row, query) for row in rows]

    # Remove exact/near-exact duplicates only. Distinct addresses remain separate
    # because they can represent legitimate branches or separate Google listings.
    seen = set()
    unique = []
    for item in candidates:
        key = (
            _fold(item["name"]),
            item["domain"],
            re.sub(r"\D", "", item["phone"] or ""),
            _fold(item["address"]),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    # Group same-name candidates to make duplicate/unit review explicit.
    groups = {}
    for item in unique:
        name_key = _fold(item["name"])
        groups.setdefault(name_key, []).append(item)

    for group_items in groups.values():
        if len(group_items) > 1:
            group_id = str(uuid.uuid4())
            for item in group_items:
                item["possible_same_entity_group"] = group_id
        else:
            group_items[0]["possible_same_entity_group"] = None

    unique.sort(
        key=lambda x: (
            x["match_score"],
            bool(x["website"]),
            bool(x["emails"]),
            x["review_count"],
        ),
        reverse=True,
    )
    for idx, item in enumerate(unique, start=1):
        item["rank"] = idx
    return unique


class EnrichmentManager:
    def __init__(self):
        self.jobs = {}
        self.jobs_lock = threading.Lock()
        self.work = queue.Queue()
        self.geocode_cache = {}
        self.geocode_lock = threading.Lock()
        worker = threading.Thread(target=self._worker_loop, daemon=True)
        worker.start()

    def submit(self, payload):
        query = self._validate_payload(payload)
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "status": "pending",
            "query": query,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        self._prune_jobs()
        with self.jobs_lock:
            self.jobs[job_id] = job
        self.work.put(job_id)
        return self.public_job(job)

    def get(self, job_id):
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            return self.public_job(job) if job else None

    def public_job(self, job):
        if not job:
            return None
        return {
            "id": job["id"],
            "status": job["status"],
            "query": job["query"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "result": job["result"],
            "error": job["error"],
        }

    def _validate_payload(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        company_name = str(payload.get("company_name", "")).strip()
        if not company_name:
            raise ValueError("company_name is required")
        city = str(payload.get("city", "")).strip()
        state = str(payload.get("state", "")).strip()
        country = str(payload.get("country", "BR")).strip() or "BR"
        depth = int(payload.get("depth", 3))
        if depth < 1 or depth > 10:
            raise ValueError("depth must be between 1 and 10")
        return {
            "company_name": company_name,
            "city": city,
            "state": state,
            "country": country,
            "depth": min(depth, MAX_DEPTH),
        }

    def _prune_jobs(self):
        cutoff = time.time() - JOB_TTL_SECONDS
        with self.jobs_lock:
            stale = [
                jid for jid, job in self.jobs.items()
                if job.get("finished_at") and job["finished_at"] < cutoff
            ]
            for jid in stale:
                self.jobs.pop(jid, None)

    def _worker_loop(self):
        while True:
            job_id = self.work.get()
            try:
                self._run_job(job_id)
            except Exception as exc:
                with self.jobs_lock:
                    job = self.jobs.get(job_id)
                    if job:
                        job["status"] = "failed"
                        job["error"] = str(exc)
                        job["finished_at"] = time.time()
                        query = job.get("query")
                    else:
                        query = None
                print(json.dumps({
                    "level": "error",
                    "component": "exporadar_enrichment",
                    "job_id": job_id,
                    "query": query,
                    "error": str(exc),
                }, ensure_ascii=False), flush=True)
            finally:
                self.work.task_done()

    def _run_job(self, job_id):
        with self.jobs_lock:
            job = self.jobs[job_id]
            job["status"] = "working"
            job["started_at"] = time.time()
            query = dict(job["query"])

        lat, lon = self._geocode(query)
        keyword = " ".join(
            part for part in [query["company_name"], query["city"], query["state"]] if part
        )
        upstream_payload = {
            "name": f"ExpoRadar {query['company_name']}",
            "keywords": [keyword],
            "lang": "pt",
            "zoom": 15,
            "lat": lat,
            "lon": lon,
            "fast_mode": False,
            "radius": 10000,
            "depth": query["depth"],
            # ExpoRadar enriches email only after the winning domain passes
            # ownership/Trust Gate. Website crawling here caused Playwright to
            # open every candidate website and pushed the 512 MiB Render service
            # into OOM restarts.
            "email": False if LIGHTWEIGHT_MODE else True,
            "max_time": 180,
        }

        upstream_id = self._create_upstream_job(upstream_payload)
        status = self._wait_upstream(upstream_id, timeout=330)
        if status != "ok":
            raise RuntimeError(f"upstream job ended with status {status}")

        rows = self._download_rows(upstream_id)
        candidates = dedupe_and_rank(rows, query)[:MAX_CANDIDATES]
        top = candidates[0] if candidates else None

        result = {
            "source": "google_maps",
            "upstream_job_id": upstream_id,
            "candidate_count": len(candidates),
            "lightweight_mode": LIGHTWEIGHT_MODE,
            "email_extraction": not LIGHTWEIGHT_MODE,
            "top_candidate": top,
            "candidates": candidates,
        }

        with self.jobs_lock:
            job = self.jobs[job_id]
            job["status"] = "ok"
            job["result"] = result
            job["finished_at"] = time.time()

    def _backend_request(self, method, path, body=None, timeout=60):
        headers = {"Content-Type": "application/json", "User-Agent": UA}
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(BACKEND + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()

    def _create_upstream_job(self, payload):
        _, raw = self._backend_request("POST", "/api/v1/jobs", payload)
        parsed = json.loads(raw.decode("utf-8"))
        job_id = parsed.get("id")
        if not job_id:
            raise RuntimeError("upstream scraper did not return a job id")
        return job_id

    def _wait_upstream(self, upstream_id, timeout=330):
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, raw = self._backend_request("GET", f"/api/v1/jobs/{upstream_id}")
            parsed = json.loads(raw.decode("utf-8"))
            status = parsed.get("Status") or parsed.get("status")
            if status in {"ok", "failed"}:
                return status
            time.sleep(5)
        raise RuntimeError("upstream scraper timed out")

    def _download_rows(self, upstream_id):
        _, raw = self._backend_request("GET", f"/api/v1/jobs/{upstream_id}/download", timeout=120)
        text = raw.decode("utf-8-sig", "replace")
        return list(csv.DictReader(io.StringIO(text)))

    def _geocode(self, query):
        place = ", ".join(
            part for part in [query.get("city"), query.get("state"), query.get("country")] if part
        )
        if not place:
            place = query["company_name"]

        cache_key = _fold(place)
        with self.geocode_lock:
            cached = self.geocode_cache.get(cache_key)
        if cached:
            return cached

        params = urllib.parse.urlencode({
            "format": "json",
            "limit": 1,
            "countrycodes": str(query.get("country") or "BR").lower(),
            "q": place,
        })
        req = urllib.request.Request(
            f"https://nominatim.openstreetmap.org/search?{params}",
            headers={"User-Agent": UA, "Accept": "application/json"},
        )

        last_error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    hits = json.loads(resp.read())
                if hits:
                    coords = (str(hits[0]["lat"]), str(hits[0]["lon"]))
                    with self.geocode_lock:
                        self.geocode_cache[cache_key] = coords
                    return coords
                last_error = RuntimeError(f"could not geocode: {place}")
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))

        raise RuntimeError(f"geocoding failed for {place}: {last_error}")
