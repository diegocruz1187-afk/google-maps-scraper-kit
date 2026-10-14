import time
import unittest

from render.enrichment import EnrichmentManager, _query_key, dedupe_and_rank, normalize_row


class EnrichmentRankingTests(unittest.TestCase):
    def test_power_dent_scores_as_strong_match(self):
        row = {
            "title": "PowerDent - Escova de Dente - Fio Dental",
            "website": "https://www.pdhb.com.br/",
            "phone": "+55 11 4161-8720",
            "emails": "mkt5@pdhb.com.br",
            "address": "Santana de Parnaíba - SP, Brasil",
            "complete_address": '{"city":"Santana de Parnaíba","state":"São Paulo","country":"BR"}',
            "review_count": "78",
            "review_rating": "4.6",
        }
        item = normalize_row(
            row,
            {"company_name": "Power Dent", "city": "São Paulo", "state": "SP"},
        )
        self.assertGreaterEqual(item["match_score"], 65)
        self.assertEqual(item["domain"], "pdhb.com.br")
        self.assertEqual(item["emails"], ["mkt5@pdhb.com.br"])

    def test_mixpac_beats_unrelated_mix_candidate(self):
        rows = [
            {
                "title": "Mix Descartáveis | Luvas, Toucas, Propé",
                "website": "http://www.mixdescartaveis.com/",
                "phone": "+55 11 99481-5810",
                "emails": "nuvempago@2x.png, visa@2x.png",
                "address": "São Paulo - SP",
                "complete_address": '{"city":"São Paulo","state":"São Paulo","country":"BR"}',
                "review_count": "107",
            },
            {
                "title": "MIXPAC Brasil",
                "website": "https://www.medmix.swiss/pt-BR",
                "phone": "+55 11 94542-1963",
                "emails": "contato_brazil@medmix.com",
                "address": "Cotia - SP",
                "complete_address": '{"city":"Cotia","state":"São Paulo","country":"BR"}',
                "review_count": "0",
            },
        ]
        ranked = dedupe_and_rank(
            rows,
            {"company_name": "MIXPAC", "city": "São Paulo", "state": "SP"},
        )
        self.assertEqual(ranked[0]["name"], "MIXPAC Brasil")
        self.assertEqual(ranked[0]["domain"], "medmix.swiss")
        self.assertEqual(ranked[0]["emails"], ["contato_brazil@medmix.com"])
        self.assertEqual(ranked[1]["emails"], [])

    def test_lmg_complete_listing_ranks_above_sparse_listing(self):
        rows = [
            {
                "title": "LMG Lasers",
                "phone": "+55 11 2361-8923",
                "address": "São Paulo - SP",
                "complete_address": '{"city":"São Paulo","state":"São Paulo","country":"BR"}',
                "review_count": "0",
            },
            {
                "title": "LMG Lasers",
                "phone": "+55 35 3559-2512",
                "website": "http://www.lmglasers.com.br/",
                "emails": "contato@lmglasers.com.br",
                "address": "São Paulo - SP",
                "complete_address": '{"city":"São Paulo","state":"São Paulo","country":"BR"}',
                "review_count": "10",
            },
        ]
        ranked = dedupe_and_rank(
            rows,
            {"company_name": "LMG Lasers", "city": "São Paulo", "state": "SP"},
        )
        self.assertEqual(ranked[0]["domain"], "lmglasers.com.br")
        self.assertGreater(ranked[0]["match_score"], ranked[1]["match_score"])
        self.assertIsNotNone(ranked[0]["possible_same_entity_group"])
        self.assertEqual(
            ranked[0]["possible_same_entity_group"],
            ranked[1]["possible_same_entity_group"],
        )

    def test_known_sao_paulo_coordinates_bypass_external_geocoder(self):
        manager = EnrichmentManager()
        query = {
            "company_name": "MIXPAC",
            "city": "São Paulo",
            "state": "SP",
            "country": "BR",
            "lat": None,
            "lon": None,
            "depth": 1,
        }
        self.assertEqual(manager._geocode(query), ("-23.5506507", "-46.6333824"))

    def test_supplied_coordinates_take_precedence(self):
        manager = EnrichmentManager()
        query = {
            "company_name": "MIXPAC",
            "city": "São Paulo",
            "state": "SP",
            "country": "BR",
            "lat": "-23.5",
            "lon": "-46.6",
            "depth": 1,
        }
        self.assertEqual(manager._geocode(query), ("-23.5", "-46.6"))

    def test_default_depth_is_one_for_bulk_safety(self):
        manager = EnrichmentManager()
        query = manager._validate_payload({
            "company_name": "MIXPAC",
            "city": "São Paulo",
            "state": "SP",
            "country": "BR",
        })
        self.assertEqual(query["depth"], 1)

    def test_duplicate_active_company_reuses_same_job(self):
        manager = EnrichmentManager()
        query = manager._validate_payload({
            "company_name": "MIXPAC",
            "city": "São Paulo",
            "state": "SP",
            "country": "BR",
        })
        existing = {
            "id": "existing-job",
            "status": "pending",
            "query": query,
            "query_key": _query_key(query),
            "deduplicated_requests": 0,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        with manager.jobs_lock:
            manager.jobs[existing["id"]] = existing

        returned = manager.submit({
            "company_name": "MIXPAC",
            "city": "São Paulo",
            "state": "SP",
            "country": "BR",
        })
        self.assertEqual(returned["id"], "existing-job")
        self.assertEqual(returned["deduplicated_requests"], 1)
        self.assertEqual(manager.work.qsize(), 0)

    def test_completed_upstream_job_is_deleted(self):
        manager = EnrichmentManager()
        calls = []

        def fake_request(method, path, body=None, timeout=60):
            calls.append((method, path, timeout))
            return 200, b""

        manager._backend_request = fake_request
        manager._delete_upstream_job("upstream-123")
        self.assertEqual(calls, [("DELETE", "/api/v1/jobs/upstream-123", 30)])


if __name__ == "__main__":
    unittest.main()
